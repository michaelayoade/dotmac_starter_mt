"""Flush-only remote-access admission and evidence owner."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_remote_access.contracts import RemoteAccessIntent, RemoteAccessRequestInput
from dotmac_remote_access.models import (
    RemoteAccessGrant,
    RemoteAccessObservation,
    RemoteAccessRequest,
)

MAX_GRANT_TTL = timedelta(hours=8)


class AccessRefused(ValueError):
    """A remote-access transition fails closed."""


def _aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def create_request(
    db: Session,
    *,
    tenant_id: UUID,
    command: RemoteAccessRequestInput,
    requested_at: datetime,
) -> RemoteAccessRequest:
    _aware(requested_at, "requested_at")
    scopes = sorted(set(command.scopes))
    if (
        not all(
            (
                command.request_key.strip(),
                command.target_ref.strip(),
                command.purpose.strip(),
                command.requester_ref.strip(),
            )
        )
        or not scopes
    ):
        raise AccessRefused(
            "target, purpose, requester and least-privilege scope are required"
        )
    digest = _digest(
        {
            "target_ref": command.target_ref,
            "purpose": command.purpose,
            "scopes": scopes,
            "requester_ref": command.requester_ref,
        }
    )
    existing = db.scalar(
        select(RemoteAccessRequest).where(
            RemoteAccessRequest.tenant_id == tenant_id,
            RemoteAccessRequest.request_key == command.request_key,
        )
    )
    if existing:
        if existing.request_digest != digest:
            raise AccessRefused("request key reused with different content")
        return existing
    row = RemoteAccessRequest(
        tenant_id=tenant_id,
        request_key=command.request_key,
        request_digest=digest,
        target_ref=command.target_ref,
        purpose=command.purpose,
        scopes=scopes,
        requester_ref=command.requester_ref,
        status="pending",
        requested_at=requested_at,
    )
    db.add(row)
    db.flush()
    return row


def admit_request(
    db: Session,
    *,
    tenant_id: UUID,
    request_id: UUID,
    approval_evidence_ref: str,
    approved_request_digest: str,
    duration: timedelta,
    admitted_at: datetime,
) -> tuple[RemoteAccessGrant, RemoteAccessIntent]:
    _aware(admitted_at, "admitted_at")
    request = db.scalar(
        select(RemoteAccessRequest).where(
            RemoteAccessRequest.tenant_id == tenant_id,
            RemoteAccessRequest.id == request_id,
        )
    )
    if request is None or request.status != "pending":
        raise AccessRefused("pending request not found")
    if request.request_digest != approved_request_digest:
        raise AccessRefused("approval digest does not bind the exact request")
    if (
        not approval_evidence_ref.strip()
        or duration <= timedelta(0)
        or duration > MAX_GRANT_TTL
    ):
        raise AccessRefused("finite grant duration exceeds the policy ceiling")
    request.status = "admitted"
    request.approval_evidence_ref = approval_evidence_ref
    request.approved_at = admitted_at
    grant = RemoteAccessGrant(
        tenant_id=tenant_id,
        request_id=request.id,
        target_ref=request.target_ref,
        scopes=list(request.scopes),
        status="active",
        admitted_at=admitted_at,
        expires_at=admitted_at + duration,
    )
    db.add(grant)
    db.flush()
    return grant, _intent(grant, "activate")


def _intent(grant: RemoteAccessGrant, action: str) -> RemoteAccessIntent:
    return RemoteAccessIntent(
        f"remote-access:{grant.id}:{action}",
        action,
        grant.id,
        grant.target_ref,
        tuple(grant.scopes),
    )


def revoke_grant(
    db: Session,
    *,
    tenant_id: UUID,
    grant_id: UUID,
    revoked_at: datetime,
    actor_ref: str,
    reason: str,
) -> RemoteAccessIntent:
    _aware(revoked_at, "revoked_at")
    grant = db.scalar(
        select(RemoteAccessGrant).where(
            RemoteAccessGrant.tenant_id == tenant_id, RemoteAccessGrant.id == grant_id
        )
    )
    if grant is None or grant.status != "active":
        raise AccessRefused("active grant not found")
    if not actor_ref.strip() or not reason.strip():
        raise AccessRefused("revocation actor and reason are required")
    grant.status = "revoked"
    grant.revoked_at = revoked_at
    grant.revocation_reason = reason
    db.flush()
    return _intent(grant, "revoke")


def expire_grants(
    db: Session, *, tenant_id: UUID, as_of: datetime
) -> tuple[RemoteAccessIntent, ...]:
    _aware(as_of, "as_of")
    rows = db.scalars(
        select(RemoteAccessGrant).where(
            RemoteAccessGrant.tenant_id == tenant_id,
            RemoteAccessGrant.status == "active",
            RemoteAccessGrant.expires_at <= as_of,
        )
    ).all()
    intents = []
    for grant in rows:
        grant.status = "expired"
        grant.revoked_at = as_of
        grant.revocation_reason = "expired"
        intents.append(_intent(grant, "revoke"))
    db.flush()
    return tuple(intents)


def record_observation(
    db: Session,
    *,
    tenant_id: UUID,
    grant_id: UUID,
    observation_key: str,
    action: str,
    outcome: str,
    observed_at: datetime,
    evidence_ref: str,
) -> RemoteAccessObservation:
    _aware(observed_at, "observed_at")
    grant = db.scalar(
        select(RemoteAccessGrant.id).where(
            RemoteAccessGrant.tenant_id == tenant_id, RemoteAccessGrant.id == grant_id
        )
    )
    if grant is None:
        raise AccessRefused("grant not found")
    digest = _digest(
        {
            "grant_id": str(grant_id),
            "action": action,
            "outcome": outcome,
            "observed_at": observed_at.isoformat(),
            "evidence_ref": evidence_ref,
        }
    )
    existing = db.scalar(
        select(RemoteAccessObservation).where(
            RemoteAccessObservation.tenant_id == tenant_id,
            RemoteAccessObservation.observation_key == observation_key,
        )
    )
    if existing:
        if existing.observation_digest != digest:
            raise AccessRefused("observation key reused with different content")
        return existing
    row = RemoteAccessObservation(
        tenant_id=tenant_id,
        grant_id=grant_id,
        observation_key=observation_key,
        observation_digest=digest,
        action=action,
        outcome=outcome,
        observed_at=observed_at,
        evidence_ref=evidence_ref,
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "AccessRefused",
    "MAX_GRANT_TTL",
    "admit_request",
    "create_request",
    "expire_grants",
    "record_observation",
    "revoke_grant",
]
