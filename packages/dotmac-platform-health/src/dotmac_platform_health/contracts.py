"""Provider-neutral inputs and views for platform health."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HealthObservationInput:
    source_ref: str
    observation_key: str
    component_code: str
    state: HealthState
    observed_at: datetime
    received_at: datetime
    summary: str
    labels: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class HealthSummary:
    component_code: str
    display_name: str
    state: str
    freshness: str
    observation_id: UUID | None
    observed_at: datetime | None
    summary: str | None


#: Self-describing schema name carried inside the canonical evidence bytes.
#: A reader (Foundation) recognizes the document by this string alone — it
#: never imports `dotmac_platform_health` to know what it is looking at.
DEPLOYMENT_HEALTH_EVIDENCE_SCHEMA = "DeploymentHealthEvidence.v1"


@dataclass(frozen=True, slots=True)
class ComponentEvidence:
    """One requested-roster entry inside canonical health evidence.

    Deliberately narrower than `HealthSummary`: no `display_name`, no free-text
    `summary` — canonical evidence contains no deployment coordinates and no
    prose, only the facts a verifier evaluates (ADR-0070 amendment,
    2026-09-07). A component that was never observed, or was never even
    registered, is represented explicitly rather than omitted: `observation_id`
    and `observed_at` are `None`, `state` is `HealthState.UNKNOWN`, and
    `freshness` is `"missing"` — the same "represent it explicitly" contract
    `summarize_health` already keeps for a component with no projection.
    """

    component_code: str
    observation_id: UUID | None
    observed_at: datetime | None
    state: str
    freshness: str


@dataclass(frozen=True, slots=True)
class DeploymentHealthEvidence:
    """The canonical, unsigned health-evidence document Platform Health
    produces from its own durable state.

    `components` holds exactly the requested roster and no more; a code the
    caller did not ask for never appears, and a code the caller DID ask for
    always appears (as a `"missing"` entry if it has no data) — this is what
    lets Foundation later require "the exact roster" (ADR-0070 amendment).
    Contains no deployment coordinates, no target, no environment: those are
    Control's to bind, not Platform Health's to know.
    """

    evaluated_at: datetime
    valid_until: datetime
    components: tuple[ComponentEvidence, ...]
