"""Provider-neutral typed reader for current operational receivables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, TypeAlias

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money

from dotmac_collections._validation import require_aware, require_text
from dotmac_collections.contracts import CollectionTiming

PositionResolution = Literal[
    "open", "partially_resolved", "resolved", "cancelled", "reversed"
]
PositionAuthority = Literal["authoritative", "shadow"]
PositionCompleteness = Literal["complete", "opening_source_incomplete"]


@dataclass(frozen=True, slots=True)
class ReceivablePositionV1:
    scope: TenantScope
    source_owner: str
    exposure_ref: str
    source_version: int
    state_fingerprint: str
    subject_ref: str
    service_ref: str | None
    collection_timing: CollectionTiming
    reason_code: str
    collectible_receivable: Money
    available_credit: Money
    funding_available: Money
    due_at: datetime | None
    coverage_start_at: datetime | None
    resolution: PositionResolution
    authority: PositionAuthority
    completeness: PositionCompleteness
    observed_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "source_owner",
            "exposure_ref",
            "state_fingerprint",
            "subject_ref",
            "reason_code",
        ):
            require_text(name, getattr(self, name))
        if self.service_ref is not None:
            require_text("service_ref", self.service_ref)
        if self.source_version < 1:
            raise ValueError("source_version must be positive")
        values = (
            self.collectible_receivable,
            self.available_credit,
            self.funding_available,
        )
        if not all(isinstance(value, Money) for value in values):
            raise TypeError("receivable values must be Money")
        if any(value.is_negative for value in values):
            raise ValueError("receivable values must be non-negative")
        if len({value.currency for value in values}) != 1:
            raise ValueError("receivable values must use one currency")
        require_aware("observed_at", self.observed_at)
        if self.due_at is not None:
            require_aware("due_at", self.due_at)
        if self.coverage_start_at is not None:
            require_aware("coverage_start_at", self.coverage_start_at)
        if self.collection_timing == "arrears":
            if self.due_at is None:
                raise ValueError("arrears position requires due_at")
        elif self.collection_timing == "advance":
            if self.coverage_start_at is None or self.due_at is not None:
                raise ValueError("advance position requires coverage_start_at only")
        else:
            raise ValueError("collection_timing is unsupported")
        if self.resolution not in (
            "open",
            "partially_resolved",
            "resolved",
            "cancelled",
            "reversed",
        ):
            raise ValueError("resolution is unsupported")
        if self.authority not in ("authoritative", "shadow"):
            raise ValueError("authority is unsupported")
        if self.completeness not in ("complete", "opening_source_incomplete"):
            raise ValueError("completeness is unsupported")


@dataclass(frozen=True, slots=True)
class PositionReadOk:
    position: ReceivablePositionV1


@dataclass(frozen=True, slots=True)
class PositionUnavailable:
    reason_code: str
    retry_after: datetime | None

    def __post_init__(self) -> None:
        require_text("reason_code", self.reason_code)
        if self.retry_after is not None:
            require_aware("retry_after", self.retry_after)


@dataclass(frozen=True, slots=True)
class PositionUnknown:
    source_owner: str
    exposure_ref: str

    def __post_init__(self) -> None:
        require_text("source_owner", self.source_owner)
        require_text("exposure_ref", self.exposure_ref)


@dataclass(frozen=True, slots=True)
class PositionAuthorityMismatch:
    expected_owner: str
    observed_owner: str

    def __post_init__(self) -> None:
        require_text("expected_owner", self.expected_owner)
        require_text("observed_owner", self.observed_owner)


ReceivablesReadResult: TypeAlias = (
    PositionReadOk | PositionUnavailable | PositionUnknown | PositionAuthorityMismatch
)


@dataclass(frozen=True, slots=True)
class ReceivablesReadCallV1:
    scope: TenantScope
    source_owner: str
    exposure_ref: str
    as_of: datetime

    def __post_init__(self) -> None:
        require_text("source_owner", self.source_owner)
        require_text("exposure_ref", self.exposure_ref)
        require_aware("as_of", self.as_of)


class ReceivablesReader(Protocol):
    def read(
        self,
        *,
        scope: TenantScope,
        source_owner: str,
        exposure_ref: str,
        as_of: datetime,
    ) -> ReceivablesReadResult: ...


class FakeReceivablesReader:
    def __init__(self) -> None:
        self._results: dict[
            tuple[TenantScope, str, str], ReceivablesReadResult
        ] = {}
        self._calls: list[ReceivablesReadCallV1] = []

    @property
    def calls(self) -> tuple[ReceivablesReadCallV1, ...]:
        return tuple(self._calls)

    def set_result(
        self,
        *,
        scope: TenantScope,
        source_owner: str,
        exposure_ref: str,
        result: ReceivablesReadResult,
    ) -> None:
        self._results[(scope, source_owner, exposure_ref)] = result

    def read(
        self,
        *,
        scope: TenantScope,
        source_owner: str,
        exposure_ref: str,
        as_of: datetime,
    ) -> ReceivablesReadResult:
        call = ReceivablesReadCallV1(scope, source_owner, exposure_ref, as_of)
        self._calls.append(call)
        key = (scope, source_owner, exposure_ref)
        if key not in self._results:
            raise AssertionError("unconfigured receivables read")
        return self._results[key]


__all__ = [
    "FakeReceivablesReader",
    "PositionAuthorityMismatch",
    "PositionReadOk",
    "PositionUnavailable",
    "PositionUnknown",
    "ReceivablePositionV1",
    "ReceivablesReadCallV1",
    "ReceivablesReadResult",
    "ReceivablesReader",
]
