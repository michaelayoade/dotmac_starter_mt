"""Flush-only writers and deterministic readers for platform health."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from dotmac_platform_health.contracts import (
    ComponentEvidence,
    DeploymentHealthEvidence,
    HealthObservationInput,
    HealthState,
    HealthSummary,
)
from dotmac_platform_health.evidence import (
    HealthEvidenceError,
    HealthEvidenceSigner,
    SignedHealthEvidence,
    canonical_health_evidence_bytes,
)
from dotmac_platform_health.models import (
    HealthComponent,
    HealthObservation,
    HealthProjection,
)


class HealthError(ValueError):
    """A health command cannot be admitted."""


class HealthConflict(HealthError):
    """A stable observation identity was reused with different content."""


@dataclass(frozen=True, slots=True)
class ObservationReceipt:
    observation: HealthObservation
    replayed: bool = False


def _aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _instant(value: datetime) -> datetime:
    """Normalize SQLite's timezone-erasing round trip for comparisons."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _fingerprint(command: HealthObservationInput) -> str:
    payload = {
        "component_code": command.component_code,
        "labels": dict(sorted(command.labels.items())),
        "observed_at": command.observed_at.isoformat(),
        "received_at": command.received_at.isoformat(),
        "state": command.state.value,
        "summary": command.summary,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def register_component(
    db: Session, *, code: str, display_name: str, freshness_seconds: int
) -> HealthComponent:
    if not code.strip() or not display_name.strip() or freshness_seconds <= 0:
        raise ValueError(
            "component code, display name and positive freshness are required"
        )
    row = db.scalar(select(HealthComponent).where(HealthComponent.code == code))
    if row is None:
        row = HealthComponent(
            code=code,
            display_name=display_name,
            freshness_seconds=freshness_seconds,
            active=True,
        )
        db.add(row)
    else:
        row.display_name = display_name
        row.freshness_seconds = freshness_seconds
        row.active = True
    db.flush()
    return row


def record_observation(
    db: Session, command: HealthObservationInput
) -> ObservationReceipt:
    _aware(command.observed_at, "observed_at")
    _aware(command.received_at, "received_at")
    if command.observed_at > command.received_at:
        raise ValueError("observed_at cannot be later than received_at")
    if len(command.labels) > 20 or any(
        len(k) > 80 or len(v) > 160 for k, v in command.labels.items()
    ):
        raise ValueError("labels must be bounded to 20 short key/value pairs")
    component = db.scalar(
        select(HealthComponent).where(
            HealthComponent.code == command.component_code,
            HealthComponent.active.is_(True),
        )
    )
    if component is None:
        raise HealthError("active component not found")
    digest = _fingerprint(command)
    existing = db.scalar(
        select(HealthObservation).where(
            HealthObservation.source_ref == command.source_ref,
            HealthObservation.observation_key == command.observation_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != digest:
            raise HealthConflict("observation key reused with different content")
        return ObservationReceipt(existing, replayed=True)
    row = HealthObservation(
        component_id=component.id,
        source_ref=command.source_ref,
        observation_key=command.observation_key,
        request_fingerprint=digest,
        state=command.state.value,
        # Snapshotted NOW, from the component's freshness policy as it stands
        # at acceptance. This value travels with the observation forever —
        # `register_component` may later widen or narrow the policy, but that
        # never retroactively changes what an already-accepted observation
        # meant. See the field's docstring on `HealthObservation`.
        freshness_seconds=component.freshness_seconds,
        observed_at=command.observed_at,
        received_at=command.received_at,
        summary=command.summary,
        labels=dict(command.labels),
    )
    freshness_seconds = component.freshness_seconds
    db.add(row)
    db.flush()
    projection = db.scalar(
        select(HealthProjection).where(HealthProjection.component_id == component.id)
    )
    if projection is None:
        projection = HealthProjection(
            component_id=component.id,
            observation_id=row.id,
            state=row.state,
            observed_at=row.observed_at,
            freshness_deadline=row.observed_at + timedelta(seconds=freshness_seconds),
        )
        db.add(projection)
    elif (_instant(row.observed_at), _instant(row.received_at), str(row.id)) > (
        _instant(projection.observed_at),
        _instant(_projection_received(db, projection)),
        str(projection.observation_id),
    ):
        projection.observation_id = row.id
        projection.state = row.state
        projection.observed_at = row.observed_at
        projection.freshness_deadline = row.observed_at + timedelta(
            seconds=freshness_seconds
        )
    db.flush()
    return ObservationReceipt(row)


def _projection_received(db: Session, projection: HealthProjection) -> datetime:
    value = db.scalar(
        select(HealthObservation.received_at).where(
            HealthObservation.id == projection.observation_id
        )
    )
    if value is None:
        raise HealthError("projection points to a missing observation")
    return value


def _require_freshness_provenance(observation: HealthObservation) -> int:
    freshness_seconds = observation.freshness_seconds
    if freshness_seconds is None:
        raise HealthError(
            "freshness provenance is unavailable for the selected observation"
        )
    return freshness_seconds


def _latest_observation(db: Session, component_id: UUID) -> HealthObservation | None:
    return db.scalars(
        select(HealthObservation)
        .where(HealthObservation.component_id == component_id)
        .order_by(
            HealthObservation.observed_at.desc(),
            HealthObservation.received_at.desc(),
            HealthObservation.id.desc(),
        )
        .limit(1)
    ).first()


def rebuild_projections(db: Session, *, rebuilt_at: datetime) -> None:
    _aware(rebuilt_at, "rebuilt_at")
    components = db.scalars(
        select(HealthComponent)
        .where(HealthComponent.active.is_(True))
        .order_by(HealthComponent.code)
    ).all()
    latest_by_component: list[tuple[HealthComponent, HealthObservation | None]] = []
    for component in components:
        latest_by_component.append(
            (
                component,
                _latest_observation(db, component.id),
            )
        )
    for _, latest in latest_by_component:
        if latest is not None:
            _require_freshness_provenance(latest)

    db.execute(delete(HealthProjection))
    for component, latest in latest_by_component:
        if latest is not None:
            db.add(
                HealthProjection(
                    component_id=component.id,
                    observation_id=latest.id,
                    state=latest.state,
                    observed_at=latest.observed_at,
                    # From the OBSERVATION's own snapshot, never from
                    # `component.freshness_seconds` — the component's current
                    # policy may have moved since `latest` was accepted, and a
                    # rebuild must reproduce the classification the observation
                    # was accepted under, not re-derive a different one under
                    # today's policy. Reading the live policy here was the
                    # freshness-policy defect: a rebuild after a policy change
                    # could flip a projection's freshness with no new
                    # observation.
                    freshness_deadline=latest.observed_at
                    + timedelta(seconds=_require_freshness_provenance(latest)),
                    created_at=rebuilt_at,
                    updated_at=rebuilt_at,
                )
            )
    db.flush()


def summarize_health(db: Session, *, as_of: datetime) -> tuple[HealthSummary, ...]:
    _aware(as_of, "as_of")
    rows = db.execute(
        select(HealthComponent, HealthProjection, HealthObservation)
        .outerjoin(
            HealthProjection, HealthProjection.component_id == HealthComponent.id
        )
        .outerjoin(
            HealthObservation, HealthObservation.id == HealthProjection.observation_id
        )
        .where(HealthComponent.active.is_(True))
        .order_by(HealthComponent.code)
    ).all()
    return tuple(
        HealthSummary(
            component.code,
            component.display_name,
            (
                projection.state
                if projection
                and observation
                and observation.freshness_seconds is not None
                else HealthState.UNKNOWN.value
            ),
            "missing"
            if projection is None
            or observation is None
            or observation.freshness_seconds is None
            else (
                "fresh" if as_of <= _instant(projection.freshness_deadline) else "stale"
            ),
            projection.observation_id if projection else None,
            _instant(projection.observed_at) if projection else None,
            observation.summary
            if observation and observation.freshness_seconds is not None
            else None,
        )
        for component, projection, observation in rows
    )


def build_health_evidence(
    db: Session, *, requested_components: Sequence[str], evaluated_at: datetime
) -> DeploymentHealthEvidence:
    """Derive the canonical, UNSIGNED health-evidence document for a roster.

    Platform Health decides state and freshness here, from its own immutable
    observations — this is the boundary the ADR-0070 amendment draws: the
    producer "stores immutably, derives `HealthState`/freshness, and produces
    the canonical, signed evidence" but "may not bind deployment
    coordinates". Signing (`produce_signed_health_evidence`) and binding
    (Control, out of scope here) both happen strictly after this call.

    `requested_components` is the EXACT roster the caller (the product's
    deployment descriptor, eventually) declares required — a code with no
    registered component and no observation is represented as an explicit
    `"missing"` entry, never silently dropped.
    """
    _aware(evaluated_at, "evaluated_at")
    if not requested_components:
        raise ValueError("requested_components must be non-empty")
    codes = tuple(requested_components)
    if len(set(codes)) != len(codes):
        raise ValueError("requested_components must not repeat a component code")
    found = {
        component.code: component
        for component in db.scalars(
            select(HealthComponent).where(HealthComponent.code.in_(codes))
        ).all()
    }
    components: list[ComponentEvidence] = []
    deadlines: list[datetime] = []
    for code in codes:
        match = found.get(code)
        if match is None:
            components.append(
                ComponentEvidence(
                    code, None, None, HealthState.UNKNOWN.value, "missing"
                )
            )
            continue
        observation = _latest_observation(db, match.id)
        if observation is None:
            components.append(
                ComponentEvidence(
                    code, None, None, HealthState.UNKNOWN.value, "missing"
                )
            )
            continue
        freshness_seconds = _require_freshness_provenance(observation)
        deadline = _instant(observation.observed_at) + timedelta(
            seconds=freshness_seconds
        )
        deadlines.append(deadline)
        components.append(
            ComponentEvidence(
                code,
                observation.id,
                _instant(observation.observed_at),
                observation.state,
                "fresh" if evaluated_at <= deadline else "stale",
            )
        )
    # Owner-derived `valid_until`: the earliest point any INCLUDED component's
    # own freshness deadline falls, i.e. the moment this evidence document
    # would stop being able to say every component is still fresh even if
    # nothing changes. A roster with no deadline at all (every entry missing)
    # is valid for no longer than the instant it was evaluated.
    valid_until = min(deadlines) if deadlines else evaluated_at
    return DeploymentHealthEvidence(
        evaluated_at=evaluated_at,
        valid_until=valid_until,
        components=tuple(sorted(components, key=lambda c: c.component_code)),
    )


def produce_signed_health_evidence(
    db: Session,
    *,
    requested_components: Sequence[str],
    evaluated_at: datetime,
    signer: HealthEvidenceSigner | None,
) -> SignedHealthEvidence:
    """Build and sign evidence only from authoritative durable state."""
    if signer is None:
        raise HealthEvidenceError("a HealthEvidenceSigner must be supplied")
    evidence = build_health_evidence(
        db, requested_components=requested_components, evaluated_at=evaluated_at
    )
    canonical_bytes = canonical_health_evidence_bytes(evidence)
    signature = signer.sign_health_evidence(evidence, canonical_bytes)
    if signature.algorithm != "ed25519":
        raise HealthEvidenceError("health evidence signatures must use ed25519")
    return SignedHealthEvidence(evidence, canonical_bytes, signature)


__all__ = [
    "HealthConflict",
    "HealthError",
    "ObservationReceipt",
    "build_health_evidence",
    "produce_signed_health_evidence",
    "rebuild_projections",
    "record_observation",
    "register_component",
    "summarize_health",
]
