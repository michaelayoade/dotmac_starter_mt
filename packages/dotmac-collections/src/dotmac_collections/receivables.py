"""Provider-neutral typed reader for current operational receivables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

from dotmac_collections._validation import require_aware, require_text
from dotmac_collections.contracts import CollectionTiming

FinancialState = Literal["open", "partially_resolved", "resolved", "cancelled"]
PositionProjectionMode = Literal["authoritative", "shadow"]
PositionCompleteness = Literal["complete", "partial", "unknown"]
ReceivableSourceAuthority = Literal["internal", "provider_owned", "external_finance"]
DueDateStatus = Literal["verified", "unknown_unverified"]
ServicePeriodStatus = Literal["not_applicable", "verified", "unknown_unverified"]


@dataclass(frozen=True, slots=True)
class ReceivableObservationV1:
    """Collections' narrow peer input, mapped by the consuming assembly."""

    scope: Scope
    source_owner: str
    exposure_ref: str
    source_version: int
    state_fingerprint: str
    subject_ref: str
    service_ref: str | None
    collection_timing: CollectionTiming
    reason_code: str
    collectible_receivable: Money
    service_period_status: ServicePeriodStatus
    service_period_starts_at: datetime | None
    service_period_ends_at: datetime | None
    due_at: datetime | None
    due_date_status: DueDateStatus
    financial_state: FinancialState
    source_authority: ReceivableSourceAuthority
    projection_mode: PositionProjectionMode
    completeness: PositionCompleteness
    completeness_reason_code: str | None
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
        if not isinstance(self.collectible_receivable, Money):
            raise TypeError("collectible_receivable must be Money")
        if self.collectible_receivable.is_negative:
            raise ValueError("collectible_receivable must be non-negative")
        require_aware("observed_at", self.observed_at)
        if self.due_at is not None:
            require_aware("due_at", self.due_at)
        if self.service_period_starts_at is not None:
            require_aware("service_period_starts_at", self.service_period_starts_at)
        if self.service_period_ends_at is not None:
            require_aware("service_period_ends_at", self.service_period_ends_at)
        if self.collection_timing not in ("advance", "arrears"):
            raise ValueError("collection_timing is unsupported")
        if self.financial_state not in (
            "open",
            "partially_resolved",
            "resolved",
            "cancelled",
        ):
            raise ValueError("financial_state is unsupported")
        if self.projection_mode not in ("authoritative", "shadow"):
            raise ValueError("projection_mode is unsupported")
        if self.source_authority not in (
            "internal",
            "provider_owned",
            "external_finance",
        ):
            raise ValueError("source_authority is unsupported")
        if self.completeness not in ("complete", "partial", "unknown"):
            raise ValueError("completeness is unsupported")
        if self.completeness == "complete":
            if self.completeness_reason_code is not None:
                raise ValueError(
                    "a complete observation cannot carry an incomplete reason"
                )
        elif not self.completeness_reason_code:
            raise ValueError("an incomplete observation requires a reason code")
        if self.due_date_status == "unknown_unverified" and self.due_at is not None:
            raise ValueError("an unverified due date cannot carry due_at")
        if self.due_date_status not in ("verified", "unknown_unverified"):
            raise ValueError("due_date_status is unsupported")
        period_values = (self.service_period_starts_at, self.service_period_ends_at)
        if self.service_period_status == "verified":
            starts_at = self.service_period_starts_at
            ends_at = self.service_period_ends_at
            if starts_at is None or ends_at is None:
                raise ValueError("a verified service period requires both instants")
            if starts_at >= ends_at:
                raise ValueError("service period start must precede end")
        elif self.service_period_status in ("not_applicable", "unknown_unverified"):
            if any(value is not None for value in period_values):
                raise ValueError("an unverified service period cannot carry instants")
        else:
            raise ValueError("service_period_status is unsupported")

    def automated_collection_blocker(self, *, as_of: datetime) -> str | None:
        """Return the first fail-closed reason, or ``None`` when action is safe."""

        require_aware("as_of", as_of)
        if self.projection_mode != "authoritative":
            return "position_not_authoritative"
        if self.completeness != "complete":
            return self.completeness_reason_code or "position_incomplete"
        if self.financial_state in {"resolved", "cancelled"}:
            return "receivable_closed"
        if self.collectible_receivable.is_zero:
            return "no_live_exposure"
        if self.due_date_status != "verified" or self.due_at is None:
            return "due_date_unverified"
        if self.due_at > as_of:
            return "receivable_not_due"
        if self.collection_timing == "advance":
            if self.service_period_status != "verified":
                return "service_period_unverified"
            if self.service_period_starts_at is not None:
                if self.service_period_starts_at > as_of:
                    return "service_period_not_started"
        return None


@dataclass(frozen=True, slots=True)
class PositionReadOk:
    position: ReceivableObservationV1


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


ReceivablesReadResult = (
    PositionReadOk | PositionUnavailable | PositionUnknown | PositionAuthorityMismatch
)


@dataclass(frozen=True, slots=True)
class ReceivablesReadCallV1:
    scope: Scope
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
        scope: Scope,
        source_owner: str,
        exposure_ref: str,
        as_of: datetime,
    ) -> ReceivablesReadResult: ...


class FakeReceivablesReader:
    def __init__(self) -> None:
        self._results: dict[tuple[Scope, str, str], ReceivablesReadResult] = {}
        self._calls: list[ReceivablesReadCallV1] = []

    @property
    def calls(self) -> tuple[ReceivablesReadCallV1, ...]:
        return tuple(self._calls)

    def set_result(
        self,
        *,
        scope: Scope,
        source_owner: str,
        exposure_ref: str,
        result: ReceivablesReadResult,
    ) -> None:
        self._results[(scope, source_owner, exposure_ref)] = result

    def read(
        self,
        *,
        scope: Scope,
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
    "ReceivableObservationV1",
    "ReceivablesReadCallV1",
    "ReceivablesReadResult",
    "ReceivablesReader",
]
