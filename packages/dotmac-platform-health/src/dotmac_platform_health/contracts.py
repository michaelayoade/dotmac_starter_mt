"""Provider-neutral inputs and views for platform health."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypedDict
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

    def __post_init__(self) -> None:
        # Freeze the caller's snapshot as well as the dataclass attributes.
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


class HealthObservationDocument(TypedDict):
    """JSON-ready representation of the existing observation input."""

    source_ref: str
    observation_key: str
    component_code: str
    state: str
    observed_at: str
    received_at: str
    summary: str
    labels: dict[str, str]


def _bounded_identifier(value: str, name: str, maximum: int) -> None:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{name} must be text of at most {maximum} characters")


def validate_health_observation(command: HealthObservationInput) -> None:
    """Check the shared storage and admission bounds without I/O.

    This validates facts, never authenticates ``source_ref``. Authenticated
    transport must establish the source before invoking the owning service.
    Summary is a Text field and has no additional length restriction here.
    """
    _bounded_identifier(command.source_ref, "source_ref", 200)
    _bounded_identifier(command.observation_key, "observation_key", 200)
    _bounded_identifier(command.component_code, "component_code", 120)
    if not isinstance(command.state, HealthState):
        raise ValueError("state must be a HealthState")
    if not isinstance(command.summary, str):
        raise ValueError("summary must be text")
    for name, value in (
        ("observed_at", command.observed_at),
        ("received_at", command.received_at),
    ):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
    if command.observed_at > command.received_at:
        raise ValueError("observed_at cannot be later than received_at")
    if len(command.labels) > 20 or any(
        not isinstance(k, str) or not isinstance(v, str) or len(k) > 80 or len(v) > 160
        for k, v in command.labels.items()
    ):
        raise ValueError("labels must be bounded to 20 short key/value pairs")


def health_observation_document(
    command: HealthObservationInput,
) -> HealthObservationDocument:
    """Validate and copy a command to JSON values, retaining timestamp offsets.

    Retries reuse this entire snapshot, including ``received_at``: changing
    payload under the same observation key is a conflict at the service.
    """
    validate_health_observation(command)
    return {
        "source_ref": command.source_ref,
        "observation_key": command.observation_key,
        "component_code": command.component_code,
        "state": command.state.value,
        "observed_at": command.observed_at.isoformat(),
        "received_at": command.received_at.isoformat(),
        "summary": command.summary,
        "labels": dict(command.labels),
    }


def health_observation_key(
    source_ref: str, component_code: str, source_observation_identity: str
) -> str:
    """Derive a versioned key solely from stable source observation identity.

    The identity must distinguish new observations at the source. Payload,
    state and receive time never enter this key; changed content for the same
    identity must conflict, not create a second observation. This hash proves
    no source authenticity. Integrator owns authenticated transport.
    """
    _bounded_identifier(source_ref, "source_ref", 200)
    _bounded_identifier(component_code, "component_code", 120)
    if (
        not isinstance(source_observation_identity, str)
        or not source_observation_identity.strip()
    ):
        raise ValueError("source_observation_identity must be nonblank text")
    domain = "health-observation:v1:"
    identity = json.dumps(
        [source_ref, component_code, source_observation_identity],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return domain + hashlib.sha256(domain.encode("ascii") + identity).hexdigest()


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
