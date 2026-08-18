"""Exact exposure membership and installment evidence for arrangements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money

from dotmac_collections._validation import require_aware, require_text


@dataclass(frozen=True, slots=True)
class ArrangementExposureV1:
    source_owner: str
    exposure_ref: str
    source_version: int
    position_fingerprint: str
    subject_ref: str
    service_ref: str | None
    admitted_amount: Money

    def __post_init__(self) -> None:
        for name in (
            "source_owner",
            "exposure_ref",
            "position_fingerprint",
            "subject_ref",
        ):
            require_text(name, getattr(self, name))
        if self.service_ref is not None:
            require_text("service_ref", self.service_ref)
        if self.source_version < 1:
            raise ValueError("source_version must be positive")
        if not isinstance(self.admitted_amount, Money):
            raise TypeError("admitted_amount must be Money")
        if self.admitted_amount.is_negative:
            raise ValueError("admitted_amount must be non-negative")


@dataclass(frozen=True, slots=True)
class InstallmentDraftV1:
    ordinal: int
    amount: Money
    due_at: datetime

    def __post_init__(self) -> None:
        if self.ordinal < 1:
            raise ValueError("ordinal must be positive")
        if not isinstance(self.amount, Money):
            raise TypeError("amount must be Money")
        if not self.amount.is_positive:
            raise ValueError("installment amount must be positive")
        require_aware("due_at", self.due_at)


@dataclass(frozen=True, slots=True)
class PaymentArrangementDraftV1:
    arrangement_id: UUID
    scope: TenantScope
    subject_ref: str
    proposed_at: datetime
    exposures: tuple[ArrangementExposureV1, ...]
    installments: tuple[InstallmentDraftV1, ...]

    def __post_init__(self) -> None:
        require_text("subject_ref", self.subject_ref)
        require_aware("proposed_at", self.proposed_at)
        if not self.exposures or not self.installments:
            raise ValueError("arrangement requires exposures and installments")
        keys = tuple((item.source_owner, item.exposure_ref) for item in self.exposures)
        if len(set(keys)) != len(keys):
            raise ValueError("arrangement exposures must be unique")
        currencies = {
            item.admitted_amount.currency for item in self.exposures
        } | {item.amount.currency for item in self.installments}
        if len(currencies) != 1:
            raise ValueError("arrangement must use one currency")
        expected = tuple(range(1, len(self.installments) + 1))
        if tuple(item.ordinal for item in self.installments) != expected:
            raise ValueError("installment ordinals must be contiguous")
        for previous, current in zip(
            self.installments, self.installments[1:], strict=False
        ):
            if current.due_at <= previous.due_at:
                raise ValueError("installment due_at values must be strictly ordered")
        exposure_total = self.exposures[0].admitted_amount
        for exposure in self.exposures[1:]:
            exposure_total = exposure_total + exposure.admitted_amount
        installment_total = self.installments[0].amount
        for installment in self.installments[1:]:
            installment_total = installment_total + installment.amount
        if exposure_total != installment_total:
            raise ValueError("installment total must equal admitted exposure total")


def arrangement_protects_exposure(
    arrangement: PaymentArrangementDraftV1,
    *,
    source_owner: str,
    exposure_ref: str,
) -> bool:
    return any(
        item.source_owner == source_owner and item.exposure_ref == exposure_ref
        for item in arrangement.exposures
    )


__all__ = [
    "ArrangementExposureV1",
    "InstallmentDraftV1",
    "PaymentArrangementDraftV1",
    "arrangement_protects_exposure",
]
