"""Flush-only durable enrollment admission, using the component lock authority."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

from dotmac_kernel.transactions import conflict_savepoint
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_platform_health.admission import (
    AuthenticatedHealthSource,
    HealthAdmission,
    HealthAdmissionError,
)
from dotmac_platform_health.contracts import (
    HealthObservationInput,
    validate_health_observation,
)
from dotmac_platform_health.models import (
    HealthComponent,
    HealthEligibilitySnapshot,
    HealthGrant,
    HealthGrantClosure,
    HealthGrantComponent,
    HealthObservation,
)

if TYPE_CHECKING:
    from dotmac_platform_health.service import ObservationReceipt


def reservation(db: Session, component_id: UUID) -> HealthGrantComponent | None:
    return db.scalar(
        select(HealthGrantComponent).where(
            HealthGrantComponent.component_id == component_id
        )
    )


def require_grant_assessment(
    db: Session, admission: HealthAdmission | None, grant_id: UUID, at: datetime
) -> tuple[HealthGrant, HealthEligibilitySnapshot]:
    if admission is None:
        raise HealthAdmissionError("reserved component requires enrollment authority")
    grant = db.get(HealthGrant, grant_id)
    if grant is None:
        raise HealthAdmissionError("grant not found")
    current = admission.current(grant.enrollment_ref, at=at)
    initial_identity = {
        key: value
        for key, value in grant.projection.items()
        if key not in {"eligible_from", "eligible_until", "standing"}
    }
    if current.identity_document() != initial_identity:
        raise HealthAdmissionError("immutable enrollment projection changed")
    members = db.scalars(
        select(HealthComponent.code)
        .join(
            HealthGrantComponent,
            HealthGrantComponent.component_id == HealthComponent.id,
        )
        .where(HealthGrantComponent.grant_id == grant.id)
    ).all()
    if sorted(members) != sorted(current.component_codes):
        raise HealthAdmissionError("permanent enrollment roster is incomplete")
    if (
        db.scalar(
            select(HealthGrantClosure.id).where(HealthGrantClosure.grant_id == grant.id)
        )
        is not None
    ):
        raise HealthAdmissionError("grant is closed")
    assessment = admission.assessment(current, at=at)
    snapshot = db.scalar(
        select(HealthEligibilitySnapshot).where(
            HealthEligibilitySnapshot.assessment_ref == assessment.assessment_ref
        )
    )
    if snapshot is not None:
        if (
            snapshot.grant_id != grant.id
            or snapshot.assessment != assessment.document()
        ):
            raise HealthAdmissionError(
                "assessment reference reused with different content"
            )
    else:
        snapshot = HealthEligibilitySnapshot(
            grant_id=grant.id,
            assessment_ref=assessment.assessment_ref,
            assessment=assessment.document(),
        )
        try:
            with conflict_savepoint(db):
                db.add(snapshot)
                db.flush()
        except IntegrityError as exc:
            snapshot = db.scalar(
                select(HealthEligibilitySnapshot).where(
                    HealthEligibilitySnapshot.assessment_ref
                    == assessment.assessment_ref
                )
            )
            if (
                snapshot is None
                or snapshot.grant_id != grant.id
                or snapshot.assessment != assessment.document()
            ):
                raise HealthAdmissionError("assessment reference conflict") from exc
    return grant, snapshot


def require_grant(
    db: Session, admission: HealthAdmission | None, grant_id: UUID, at: datetime
) -> HealthGrant:
    return require_grant_assessment(db, admission, grant_id, at)[0]


def require_signing_roster(
    db: Session, admission: HealthAdmission | None, codes: Sequence[str], at: datetime
) -> None:
    if admission is None:
        raise HealthAdmissionError("deployment signing requires enrollment authority")
    members = db.execute(
        select(HealthComponent, HealthGrantComponent)
        .join(
            HealthGrantComponent,
            HealthGrantComponent.component_id == HealthComponent.id,
        )
        .where(HealthComponent.code.in_(codes))
        .order_by(HealthComponent.code)
        .with_for_update()
    ).all()
    if len(members) != len(codes) or any(
        not component.active for component, _ in members
    ):
        raise HealthAdmissionError(
            "deployment signing requires exact active grant roster"
        )
    grants = {membership.grant_id for _, membership in members}
    if len(grants) != 1:
        raise HealthAdmissionError("deployment signing cannot mix enrollment rosters")
    grant = require_grant(db, admission, next(iter(grants)), at)
    if sorted(codes) != grant.projection["component_codes"]:
        raise HealthAdmissionError("deployment signing requires exact permanent roster")


def create_grant(
    db: Session, admission: HealthAdmission, *, enrollment_ref: str, at: datetime
) -> HealthGrant:
    projection = admission.current(enrollment_ref, at=at)
    admission.assessment(projection, at=at)
    # Shared lock with legacy and authenticated writers. Ordered roster locking
    # prevents deadlocks between overlapping, contradictory enrollment rosters.
    components = db.scalars(
        select(HealthComponent)
        .where(HealthComponent.code.in_(projection.component_codes))
        .order_by(HealthComponent.code)
        .with_for_update()
    ).all()
    if len(components) != len(projection.component_codes) or any(
        not component.active for component in components
    ):
        raise HealthAdmissionError("enrollment requires exact active component roster")
    existing = db.scalar(
        select(HealthGrant).where(HealthGrant.enrollment_ref == enrollment_ref)
    )
    if existing is not None:
        require_grant(db, admission, existing.id, at)
        return existing
    if any(reservation(db, component.id) is not None for component in components):
        raise HealthAdmissionError("component is permanently reserved to another grant")
    grant = HealthGrant(
        enrollment_ref=projection.enrollment_ref,
        installation_ref=projection.installation_ref,
        projection_fingerprint=projection.fingerprint,
        projection=projection.document(),
    )
    try:
        with conflict_savepoint(db):
            db.add(grant)
            db.flush()
            for component in components:
                db.add(
                    HealthGrantComponent(grant_id=grant.id, component_id=component.id)
                )
            db.flush()
    except IntegrityError as exc:
        # Different rosters with the same installation/enrollment still contend
        # on database uniqueness even when they hold disjoint component locks.
        existing = db.scalar(
            select(HealthGrant).where(HealthGrant.enrollment_ref == enrollment_ref)
        )
        if (
            existing is None
            or {
                key: value
                for key, value in existing.projection.items()
                if key not in {"eligible_from", "eligible_until", "standing"}
            }
            != projection.identity_document()
        ):
            raise HealthAdmissionError(
                "permanent enrollment or installation conflict"
            ) from exc
        require_grant(db, admission, existing.id, at)
        return existing
    require_grant(db, admission, grant.id, at)
    return grant


def close_grant(
    db: Session, *, grant_id: UUID, closed_at: datetime, reason: str
) -> HealthGrantClosure:
    if closed_at.utcoffset() is None or not reason.strip() or len(reason) > 200:
        raise HealthAdmissionError("aware closure time and bounded reason required")
    db.scalars(
        select(HealthComponent)
        .join(
            HealthGrantComponent,
            HealthGrantComponent.component_id == HealthComponent.id,
        )
        .where(HealthGrantComponent.grant_id == grant_id)
        .order_by(HealthComponent.code)
        .with_for_update()
    ).all()
    if db.get(HealthGrant, grant_id) is None:
        raise HealthAdmissionError("grant not found")
    existing = db.scalar(
        select(HealthGrantClosure).where(HealthGrantClosure.grant_id == grant_id)
    )
    if existing is not None:
        return existing
    closure = HealthGrantClosure(grant_id=grant_id, closed_at=closed_at, reason=reason)
    db.add(closure)
    db.flush()
    return closure


def record_authenticated_observation(
    db: Session,
    admission: HealthAdmission,
    *,
    grant_id: UUID,
    source: AuthenticatedHealthSource,
    command: HealthObservationInput,
    at: datetime,
) -> ObservationReceipt:
    from dotmac_platform_health.service import (
        HealthConflict,
        ObservationReceipt,
        _fingerprint,
        _record_observation,
    )

    validate_health_observation(command)
    if not isinstance(source, AuthenticatedHealthSource):
        raise HealthAdmissionError("authenticated source value required")
    component = db.scalar(
        select(HealthComponent)
        .where(HealthComponent.code == command.component_code)
        .with_for_update()
    )
    grant = db.get(HealthGrant, grant_id)
    if grant is None or component is None:
        raise HealthAdmissionError("grant or component binding not found")
    if (
        any(
            getattr(source, name) != grant.projection[name]
            for name in source.__dataclass_fields__
        )
        or command.source_ref != source.source_ref
    ):
        raise HealthAdmissionError("authenticated source binding mismatch")
    membership = reservation(db, component.id)
    if (
        membership is None
        or membership.grant_id != grant.id
        or command.component_code
        not in cast(list[str], grant.projection["component_codes"])
    ):
        raise HealthAdmissionError("component roster binding mismatch")
    existing = db.scalar(
        select(HealthObservation).where(
            HealthObservation.source_ref == command.source_ref,
            HealthObservation.observation_key == command.observation_key,
        )
    )
    if existing is not None:
        if (
            existing.grant_id != grant.id
            or existing.request_fingerprint != _fingerprint(command)
        ):
            raise HealthConflict(
                "observation key reused with different content or grant"
            )
        return ObservationReceipt(existing, replayed=True)
    _, assessment = require_grant_assessment(db, admission, grant.id, at)
    if not component.active:
        raise HealthAdmissionError("active component not found")
    return _record_observation(
        db, command, grant_id=grant.id, assessment_id=assessment.id
    )
