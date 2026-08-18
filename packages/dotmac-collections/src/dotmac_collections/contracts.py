"""Ingress identity contract for a request to reassess one exposure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from dotmac_kernel.cache import TenantScope

from dotmac_collections._validation import require_aware, require_text

CollectionTiming = Literal["advance", "arrears"]


@dataclass(frozen=True, slots=True)
class TriggerProvenanceV1:
    kind: str
    trigger_id: str
    triggered_at: datetime

    def __post_init__(self) -> None:
        require_text("kind", self.kind)
        require_text("trigger_id", self.trigger_id)
        require_aware("triggered_at", self.triggered_at)


@dataclass(frozen=True, slots=True)
class AssessCollectionExposureV1:
    """Identity and provenance only; current money is always reread."""

    command_id: UUID
    idempotency_key: str
    correlation_id: UUID
    causal_event_id: str
    scope: TenantScope
    source_owner: str
    exposure_ref: str
    subject_ref: str
    service_ref: str | None
    collection_timing: CollectionTiming
    reason_code: str
    trigger: TriggerProvenanceV1

    def __post_init__(self) -> None:
        for name in (
            "idempotency_key",
            "causal_event_id",
            "source_owner",
            "exposure_ref",
            "subject_ref",
            "reason_code",
        ):
            require_text(name, getattr(self, name))
        if self.service_ref is not None:
            require_text("service_ref", self.service_ref)
        if self.collection_timing not in ("advance", "arrears"):
            raise ValueError("collection_timing is unsupported")


__all__ = ["AssessCollectionExposureV1", "CollectionTiming", "TriggerProvenanceV1"]
