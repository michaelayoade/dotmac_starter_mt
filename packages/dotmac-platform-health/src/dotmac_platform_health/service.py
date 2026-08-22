"""Flush-only writers and deterministic readers for platform health."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from dotmac_platform_health.contracts import (
    HealthObservationInput,
    HealthState,
    HealthSummary,
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
        observed_at=command.observed_at,
        received_at=command.received_at,
        summary=command.summary,
        labels=dict(command.labels),
    )
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
            freshness_deadline=row.observed_at
            + timedelta(seconds=component.freshness_seconds),
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
            seconds=component.freshness_seconds
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


def rebuild_projections(db: Session, *, rebuilt_at: datetime) -> None:
    _aware(rebuilt_at, "rebuilt_at")
    db.execute(delete(HealthProjection))
    components = db.scalars(
        select(HealthComponent)
        .where(HealthComponent.active.is_(True))
        .order_by(HealthComponent.code)
    ).all()
    for component in components:
        latest = db.scalars(
            select(HealthObservation)
            .where(HealthObservation.component_id == component.id)
            .order_by(
                HealthObservation.observed_at.desc(),
                HealthObservation.received_at.desc(),
                HealthObservation.id.desc(),
            )
            .limit(1)
        ).first()
        if latest is not None:
            db.add(
                HealthProjection(
                    component_id=component.id,
                    observation_id=latest.id,
                    state=latest.state,
                    observed_at=latest.observed_at,
                    freshness_deadline=latest.observed_at
                    + timedelta(seconds=component.freshness_seconds),
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
            projection.state if projection else HealthState.UNKNOWN.value,
            "missing"
            if projection is None
            else (
                "fresh" if as_of <= _instant(projection.freshness_deadline) else "stale"
            ),
            projection.observation_id if projection else None,
            _instant(projection.observed_at) if projection else None,
            observation.summary if observation else None,
        )
        for component, projection, observation in rows
    )


__all__ = [
    "HealthConflict",
    "HealthError",
    "ObservationReceipt",
    "rebuild_projections",
    "record_observation",
    "register_component",
    "summarize_health",
]
