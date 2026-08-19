"""Flush-only access projection, observation, session, and drift owner."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_network_access.contracts import (
    AccessDriftReport,
    AccessProjection,
    AccessState,
    AccessStateQuery,
    AccountingQuery,
    AccountingReceipt,
    AuthenticationOutcome,
    AuthenticationQuery,
    AuthenticationReceipt,
    CloseSession,
    ProjectAccessPolicy,
    ReconcileAccess,
    RecordAccounting,
    RecordAuthentication,
    RegisterNasAttachment,
    SessionQuery,
    SessionSnapshot,
    SessionState,
)
from dotmac_network_access.models import (
    AccessEvent,
    AccessProjectionRow,
    AccessReconciliation,
    AccessSession,
    AccountingObservation,
    AuthenticationObservation,
    NasAttachment,
)


class AccessError(ValueError):
    pass


class AccessNotFound(AccessError):
    pass


class AccessConflict(AccessError):
    pass



def _stored_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive round-trip without weakening ingress.

    Ingress values are tz-aware; PostgreSQL returns them that way and SQLite
    does not. Comparing the two directly raises, so the STORED side is
    normalized at the point of comparison rather than the incoming side, which
    would quietly accept a naive value from a caller.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

def _clean(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise AccessError(f"{label} must not be blank")
    return result


def _fingerprint(command: ProjectAccessPolicy) -> str:
    material = "\x1f".join(
        (
            command.subject_ref.strip(),
            command.desired_state.value,
            command.policy_code.strip(),
            command.policy_version.strip(),
            command.decision_ref.strip(),
            *(
                f"{key.strip()}={value.strip()}"
                for key, value in sorted(command.attributes)
            ),
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _event(
    db: Session,
    *,
    tenant_id: UUID,
    aggregate_ref: str,
    event_type: str,
    evidence_ref: str,
    payload: dict[str, str],
    occurred_at: datetime,
) -> None:
    db.add(
        AccessEvent(
            tenant_id=tenant_id,
            aggregate_ref=aggregate_ref,
            event_type=event_type,
            evidence_ref=evidence_ref,
            payload=payload,
            occurred_at=occurred_at,
        )
    )


def _projection(row: AccessProjectionRow) -> AccessProjection:
    return AccessProjection(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        desired_state=AccessState(row.desired_state),
        policy_code=row.policy_code,
        policy_version=row.policy_version,
        attributes=tuple((pair[0], pair[1]) for pair in row.attributes),
        decision_ref=row.decision_ref,
        desired_fingerprint=row.desired_fingerprint,
        observed_fingerprint=row.observed_fingerprint,
        valid_until=row.valid_until,
        projected_at=row.projected_at,
    )


def _session(row: AccessSession) -> SessionSnapshot:
    return SessionSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        nas_ref=row.nas_ref,
        session_ref=row.session_ref,
        state=SessionState(row.state),
        started_at=row.started_at,
        last_seen_at=row.last_seen_at,
        closed_at=row.closed_at,
        closed_reason_code=row.closed_reason_code,
        close_source_ref=row.close_source_ref,
        input_octets=row.input_octets,
        output_octets=row.output_octets,
    )


def register_nas_attachment(
    db: Session, *, tenant_id: UUID, command: RegisterNasAttachment
) -> UUID:
    row = NasAttachment(
        tenant_id=tenant_id,
        nas_ref=_clean(command.nas_ref, "NAS reference"),
        access_server_ref=_clean(command.access_server_ref, "access server reference"),
        capability_code=_clean(command.capability_code, "capability code"),
        source_ref=_clean(command.source_ref, "source reference"),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise AccessConflict("NAS attachment already exists") from exc
    return row.id


def project_access_policy(
    db: Session, *, tenant_id: UUID, command: ProjectAccessPolicy
) -> AccessProjection:
    subject_ref = _clean(command.subject_ref, "subject reference")
    row = db.scalar(
        select(AccessProjectionRow)
        .where(
            AccessProjectionRow.tenant_id == tenant_id,
            AccessProjectionRow.subject_ref == subject_ref,
        )
        .with_for_update()
    )
    desired = _fingerprint(command)
    changed = row is None or row.desired_fingerprint != desired
    now = datetime.now(UTC)
    if row is None:
        row = AccessProjectionRow(
            tenant_id=tenant_id,
            subject_ref=subject_ref,
            desired_state=command.desired_state.value,
            policy_code=_clean(command.policy_code, "policy code"),
            policy_version=_clean(command.policy_version, "policy version"),
            attributes=[list(pair) for pair in command.attributes],
            decision_ref=_clean(command.decision_ref, "decision reference"),
            desired_fingerprint=desired,
            valid_until=command.valid_until,
            projected_at=now,
        )
        from dotmac_kernel.db import conflict_savepoint

        try:
            with conflict_savepoint(db):
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            raise AccessConflict("access projection changed concurrently") from exc
    else:
        row.desired_state = command.desired_state.value
        row.policy_code = _clean(command.policy_code, "policy code")
        row.policy_version = _clean(command.policy_version, "policy version")
        row.attributes = [list(pair) for pair in command.attributes]
        row.decision_ref = _clean(command.decision_ref, "decision reference")
        row.desired_fingerprint = desired
        row.valid_until = command.valid_until
        row.projected_at = now
    if changed:
        _event(
            db,
            tenant_id=tenant_id,
            aggregate_ref=f"access-projection:{row.id}",
            event_type="access_projection_changed",
            evidence_ref=row.decision_ref,
            payload={"desired_state": row.desired_state},
            occurred_at=now,
        )
    db.flush()
    return _projection(row)


def record_authentication(
    db: Session, *, tenant_id: UUID, command: RecordAuthentication
) -> AuthenticationReceipt:
    existing = db.scalar(
        select(AuthenticationObservation).where(
            AuthenticationObservation.tenant_id == tenant_id,
            AuthenticationObservation.source_ref == command.source_ref,
            AuthenticationObservation.fingerprint == command.fingerprint,
        )
    )
    duplicate = existing is not None
    if existing is None:
        row = AuthenticationObservation(
            tenant_id=tenant_id,
            subject_ref=_clean(command.subject_ref, "subject reference"),
            nas_ref=_clean(command.nas_ref, "NAS reference"),
            session_ref=command.session_ref,
            outcome=command.outcome.value,
            reason_code=command.reason_code,
            source_ref=_clean(command.source_ref, "source reference"),
            observed_at=command.observed_at,
            fingerprint=_clean(command.fingerprint, "fingerprint"),
        )
        from dotmac_kernel.db import conflict_savepoint

        try:
            with conflict_savepoint(db):
                db.add(row)
                db.flush()
                _event(
                    db,
                    tenant_id=tenant_id,
                    aggregate_ref=f"authentication:{row.id}",
                    event_type="authentication_observed",
                    evidence_ref=row.source_ref,
                    payload={"outcome": row.outcome},
                    occurred_at=row.observed_at,
                )
                db.flush()
        except IntegrityError as exc:
            raise AccessConflict("authentication fingerprint already exists") from exc
    else:
        row = existing
        if (
            row.outcome != command.outcome.value
            or row.subject_ref != command.subject_ref
        ):
            raise AccessConflict(
                "authentication fingerprint reused with different facts"
            )
    return AuthenticationReceipt(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        nas_ref=row.nas_ref,
        session_ref=row.session_ref,
        outcome=AuthenticationOutcome(row.outcome),
        reason_code=row.reason_code,
        source_ref=row.source_ref,
        observed_at=row.observed_at,
        fingerprint=row.fingerprint,
        duplicate=duplicate,
    )


def record_accounting(
    db: Session, *, tenant_id: UUID, command: RecordAccounting
) -> AccountingReceipt:
    if min(command.input_octets, command.output_octets, command.session_seconds) < 0:
        raise AccessError("accounting counters cannot be negative")
    existing = db.scalar(
        select(AccountingObservation).where(
            AccountingObservation.tenant_id == tenant_id,
            AccountingObservation.source_ref == command.source_ref,
            AccountingObservation.fingerprint == command.fingerprint,
        )
    )
    duplicate = existing is not None
    if existing is None:
        row = AccountingObservation(
            tenant_id=tenant_id,
            subject_ref=_clean(command.subject_ref, "subject reference"),
            nas_ref=_clean(command.nas_ref, "NAS reference"),
            session_ref=_clean(command.session_ref, "session reference"),
            event_kind=_clean(command.event_kind, "event kind").lower(),
            input_octets=command.input_octets,
            output_octets=command.output_octets,
            session_seconds=command.session_seconds,
            source_ref=_clean(command.source_ref, "source reference"),
            observed_at=command.observed_at,
            fingerprint=_clean(command.fingerprint, "fingerprint"),
        )
        from dotmac_kernel.db import conflict_savepoint

        try:
            with conflict_savepoint(db):
                db.add(row)
                session = db.scalar(
                    select(AccessSession)
                    .where(
                        AccessSession.tenant_id == tenant_id,
                        AccessSession.session_ref == row.session_ref,
                    )
                    .with_for_update()
                )
                started = session is None
                if session is None:
                    session = AccessSession(
                        tenant_id=tenant_id,
                        subject_ref=row.subject_ref,
                        nas_ref=row.nas_ref,
                        session_ref=row.session_ref,
                        state=SessionState.ACTIVE.value,
                        started_at=row.observed_at
                        - timedelta(seconds=row.session_seconds),
                        last_seen_at=row.observed_at,
                        input_octets=row.input_octets,
                        output_octets=row.output_octets,
                    )
                    db.add(session)
                else:
                    if (
                        session.subject_ref != row.subject_ref
                        or session.nas_ref != row.nas_ref
                    ):
                        raise AccessConflict(
                            "session reference reused for another subject or NAS"
                        )
                    session.last_seen_at = max(
                        _stored_utc(session.last_seen_at), row.observed_at
                    )
                    session.input_octets = max(session.input_octets, row.input_octets)
                    session.output_octets = max(
                        session.output_octets, row.output_octets
                    )
                if row.event_kind in {"stop", "closed"}:
                    session.state = SessionState.CLOSED.value
                    session.closed_at = row.observed_at
                    session.closed_reason_code = "accounting-stop"
                    session.close_source_ref = row.source_ref
                db.flush()
                if started:
                    _event(
                        db,
                        tenant_id=tenant_id,
                        aggregate_ref=f"session:{session.id}",
                        event_type="session_started",
                        evidence_ref=row.source_ref,
                        payload={"session_ref": session.session_ref},
                        occurred_at=_stored_utc(session.started_at),
                    )
                if row.event_kind in {"stop", "closed"}:
                    _event(
                        db,
                        tenant_id=tenant_id,
                        aggregate_ref=f"session:{session.id}",
                        event_type="session_closed",
                        evidence_ref=row.source_ref,
                        payload={"reason_code": "accounting-stop"},
                        occurred_at=row.observed_at,
                    )
                db.flush()
        except IntegrityError as exc:
            raise AccessConflict("accounting fingerprint or session conflicts") from exc
    else:
        row = existing
        if (
            row.subject_ref != command.subject_ref
            or row.event_kind != command.event_kind.lower()
        ):
            raise AccessConflict("accounting fingerprint reused with different facts")
    return AccountingReceipt(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        nas_ref=row.nas_ref,
        session_ref=row.session_ref,
        event_kind=row.event_kind,
        input_octets=row.input_octets,
        output_octets=row.output_octets,
        session_seconds=row.session_seconds,
        source_ref=row.source_ref,
        observed_at=row.observed_at,
        fingerprint=row.fingerprint,
        duplicate=duplicate,
    )


def reconcile_access(
    db: Session, *, tenant_id: UUID, command: ReconcileAccess
) -> AccessDriftReport:
    row = db.scalar(
        select(AccessProjectionRow)
        .where(
            AccessProjectionRow.tenant_id == tenant_id,
            AccessProjectionRow.subject_ref == command.subject_ref,
        )
        .with_for_update()
    )
    if row is None:
        raise AccessNotFound("access projection not found")
    drifted = (
        row.desired_fingerprint != command.observed_fingerprint
        or row.desired_state != command.observed_state.value
    )
    row.observed_fingerprint = _clean(
        command.observed_fingerprint, "observed fingerprint"
    )
    reason = "fingerprint-or-state-mismatch" if drifted else None
    db.add(
        AccessReconciliation(
            tenant_id=tenant_id,
            subject_ref=row.subject_ref,
            expected_fingerprint=row.desired_fingerprint,
            observed_fingerprint=row.observed_fingerprint,
            drifted=drifted,
            reason_code=reason,
            source_ref=_clean(command.source_ref, "source reference"),
            reconciled_at=command.observed_at,
        )
    )
    if drifted:
        _event(
            db,
            tenant_id=tenant_id,
            aggregate_ref=f"access-projection:{row.id}",
            event_type="access_drift_detected",
            evidence_ref=command.source_ref,
            payload={"reason_code": reason or ""},
            occurred_at=command.observed_at,
        )
    db.flush()
    return AccessDriftReport(
        projection=_projection(row),
        drifted=drifted,
        expected_fingerprint=row.desired_fingerprint,
        observed_fingerprint=row.observed_fingerprint,
        reason_code=reason,
        reconciled_at=command.observed_at,
    )


def close_session(
    db: Session, *, tenant_id: UUID, command: CloseSession
) -> SessionSnapshot:
    row = db.scalar(
        select(AccessSession)
        .where(
            AccessSession.tenant_id == tenant_id, AccessSession.id == command.session_id
        )
        .with_for_update()
    )
    if row is None:
        raise AccessNotFound("session not found")
    if (
        SessionState(row.state) is not command.expected
        or command.expected is not SessionState.ACTIVE
    ):
        raise AccessConflict("session state changed")
    row.state = SessionState.CLOSED.value
    row.closed_at = command.closed_at
    row.closed_reason_code = _clean(command.reason_code, "close reason")
    row.close_source_ref = _clean(command.source_ref, "source reference")
    row.last_seen_at = max(_stored_utc(row.last_seen_at), command.closed_at)
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"session:{row.id}",
        event_type="session_closed",
        evidence_ref=row.close_source_ref,
        payload={"reason_code": row.closed_reason_code},
        occurred_at=command.closed_at,
    )
    db.flush()
    return _session(row)


def query_access_state(
    db: Session, *, tenant_id: UUID, query: AccessStateQuery
) -> AccessProjection | None:
    row = db.scalar(
        select(AccessProjectionRow).where(
            AccessProjectionRow.tenant_id == tenant_id,
            AccessProjectionRow.subject_ref == query.subject_ref,
        )
    )
    return _projection(row) if row is not None else None


def query_sessions(
    db: Session, *, tenant_id: UUID, query: SessionQuery
) -> tuple[SessionSnapshot, ...]:
    statement = select(AccessSession).where(AccessSession.tenant_id == tenant_id)
    if query.session_id is not None:
        statement = statement.where(AccessSession.id == query.session_id)
    if query.subject_ref is not None:
        statement = statement.where(AccessSession.subject_ref == query.subject_ref)
    if query.active_only:
        statement = statement.where(AccessSession.state == SessionState.ACTIVE.value)
    return tuple(_session(row) for row in db.scalars(statement))


def query_authentication(
    db: Session, *, tenant_id: UUID, query: AuthenticationQuery
) -> tuple[AuthenticationReceipt, ...]:
    statement = select(AuthenticationObservation).where(
        AuthenticationObservation.tenant_id == tenant_id,
        AuthenticationObservation.subject_ref == query.subject_ref,
    )
    if query.since is not None:
        statement = statement.where(
            AuthenticationObservation.observed_at >= query.since
        )
    return tuple(
        AuthenticationReceipt(
            id=row.id,
            tenant_id=row.tenant_id,
            subject_ref=row.subject_ref,
            nas_ref=row.nas_ref,
            session_ref=row.session_ref,
            outcome=AuthenticationOutcome(row.outcome),
            reason_code=row.reason_code,
            source_ref=row.source_ref,
            observed_at=row.observed_at,
            fingerprint=row.fingerprint,
            duplicate=False,
        )
        for row in db.scalars(statement)
    )


def query_accounting(
    db: Session, *, tenant_id: UUID, query: AccountingQuery
) -> tuple[AccountingReceipt, ...]:
    statement = select(AccountingObservation).where(
        AccountingObservation.tenant_id == tenant_id
    )
    if query.session_ref is not None:
        statement = statement.where(
            AccountingObservation.session_ref == query.session_ref
        )
    if query.subject_ref is not None:
        statement = statement.where(
            AccountingObservation.subject_ref == query.subject_ref
        )
    if query.since is not None:
        statement = statement.where(AccountingObservation.observed_at >= query.since)
    return tuple(
        AccountingReceipt(
            id=row.id,
            tenant_id=row.tenant_id,
            subject_ref=row.subject_ref,
            nas_ref=row.nas_ref,
            session_ref=row.session_ref,
            event_kind=row.event_kind,
            input_octets=row.input_octets,
            output_octets=row.output_octets,
            session_seconds=row.session_seconds,
            source_ref=row.source_ref,
            observed_at=row.observed_at,
            fingerprint=row.fingerprint,
            duplicate=False,
        )
        for row in db.scalars(statement)
    )


__all__ = [
    "AccessConflict",
    "AccessError",
    "AccessNotFound",
    "close_session",
    "project_access_policy",
    "query_access_state",
    "query_accounting",
    "query_authentication",
    "query_sessions",
    "reconcile_access",
    "record_accounting",
    "record_authentication",
    "register_nas_attachment",
]
