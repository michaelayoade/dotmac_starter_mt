"""Flush-only owner of temporary support-access admission and closure."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_support_access.contracts import AccessMode, SupportRequestInput
from dotmac_support_access.models import (
    SupportAccessEvent,
    SupportAccessGrant,
    SupportAccessRequest,
)

CONSENT_MAX_TTL = timedelta(hours=8)
BREAK_GLASS_MAX_TTL = timedelta(minutes=15)


class AccessRefused(ValueError):
    """A temporary-access transition fails closed."""


def _aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _digest(command: SupportRequestInput) -> str:
    payload = {
        "case_ref": command.case_ref,
        "purpose": command.purpose,
        "target_ref": command.target_ref,
        "requester_ref": command.requester_ref,
        "capabilities": sorted(set(command.capabilities)),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _event(
    db: Session,
    request: SupportAccessRequest,
    event_type: str,
    occurred_at: datetime,
    *,
    grant_id: UUID | None = None,
    actor_ref: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    sequence = (
        db.scalar(
            select(func.max(SupportAccessEvent.sequence)).where(
                SupportAccessEvent.request_id == request.id
            )
        )
        or 0
    )
    db.add(
        SupportAccessEvent(
            request_id=request.id,
            grant_id=grant_id,
            sequence=sequence + 1,
            event_type=event_type,
            actor_ref=actor_ref,
            occurred_at=occurred_at,
            details=details or {},
        )
    )


def create_request(
    db: Session, command: SupportRequestInput, *, requested_at: datetime
) -> SupportAccessRequest:
    _aware(requested_at, "requested_at")
    capabilities = sorted(set(command.capabilities))
    if (
        not command.request_key.strip()
        or not command.case_ref.strip()
        or not command.purpose.strip()
        or not command.target_ref.strip()
        or not command.requester_ref.strip()
        or not capabilities
    ):
        raise AccessRefused(
            "case, purpose, target, requester and least-privilege "
            "capabilities are required"
        )
    digest = _digest(command)
    existing = db.scalar(
        select(SupportAccessRequest).where(
            SupportAccessRequest.request_key == command.request_key
        )
    )
    if existing is not None:
        if existing.request_digest != digest:
            raise AccessRefused("request key reused with different content")
        return existing
    row = SupportAccessRequest(
        request_key=command.request_key,
        request_digest=digest,
        case_ref=command.case_ref,
        purpose=command.purpose,
        target_ref=command.target_ref,
        requester_ref=command.requester_ref,
        capabilities=capabilities,
        status="pending",
        requested_at=requested_at,
    )
    db.add(row)
    db.flush()
    _event(db, row, "requested", requested_at, actor_ref=command.requester_ref)
    db.flush()
    return row


def admit_request(
    db: Session,
    *,
    request_id: UUID,
    approval_evidence_ref: str,
    approved_request_digest: str,
    mode: AccessMode,
    duration: timedelta,
    admitted_at: datetime,
    consent_evidence_ref: str | None = None,
    break_glass_reason: str | None = None,
) -> SupportAccessGrant:
    _aware(admitted_at, "admitted_at")
    request = db.get(SupportAccessRequest, request_id)
    if request is None:
        raise AccessRefused("support request not found")
    if request.status != "pending":
        raise AccessRefused("request was already admitted or closed")
    if request.request_digest != approved_request_digest:
        raise AccessRefused("approval digest does not bind the exact request")
    if not approval_evidence_ref.strip() or duration <= timedelta(0):
        raise AccessRefused(
            "approval evidence and positive finite duration are required"
        )
    ceiling = CONSENT_MAX_TTL if mode is AccessMode.CONSENT else BREAK_GLASS_MAX_TTL
    if duration > ceiling:
        label = "consent" if mode is AccessMode.CONSENT else "break-glass"
        raise AccessRefused(f"{label} duration exceeds its policy ceiling")
    if mode is AccessMode.CONSENT and not consent_evidence_ref:
        raise AccessRefused("consent admission requires consent evidence")
    if mode is AccessMode.BREAK_GLASS and not break_glass_reason:
        raise AccessRefused("break-glass admission requires an incident reason")
    request.status = "admitted"
    request.approval_evidence_ref = approval_evidence_ref
    request.approved_at = admitted_at
    grant = SupportAccessGrant(
        request_id=request.id,
        case_ref=request.case_ref,
        purpose=request.purpose,
        target_ref=request.target_ref,
        requester_ref=request.requester_ref,
        capabilities=list(request.capabilities),
        mode=mode.value,
        consent_evidence_ref=consent_evidence_ref,
        break_glass_reason=break_glass_reason,
        status="active",
        issued_at=admitted_at,
        expires_at=admitted_at + duration,
    )
    db.add(grant)
    db.flush()
    _event(
        db,
        request,
        "admitted",
        admitted_at,
        grant_id=grant.id,
        details={"mode": mode.value, "expires_at": grant.expires_at.isoformat()},
    )
    db.flush()
    return grant


def revoke_grant(
    db: Session, *, grant_id: UUID, revoked_at: datetime, actor_ref: str, reason: str
) -> SupportAccessGrant:
    _aware(revoked_at, "revoked_at")
    grant = db.get(SupportAccessGrant, grant_id)
    if grant is None or grant.status != "active":
        raise AccessRefused("only an active grant can be revoked")
    grant.status = "revoked"
    grant.revoked_at = revoked_at
    grant.revocation_reason = reason
    request = db.get(SupportAccessRequest, grant.request_id)
    if request is None:
        raise AccessRefused("grant request evidence is missing")
    request.status = "closed"
    _event(
        db,
        request,
        "revoked",
        revoked_at,
        grant_id=grant.id,
        actor_ref=actor_ref,
        details={"reason": reason},
    )
    db.flush()
    return grant


def expire_grants(db: Session, *, as_of: datetime) -> int:
    _aware(as_of, "as_of")
    rows = db.scalars(
        select(SupportAccessGrant).where(
            SupportAccessGrant.status == "active",
            SupportAccessGrant.expires_at <= as_of,
        )
    ).all()
    for grant in rows:
        grant.status = "expired"
        request = db.get(SupportAccessRequest, grant.request_id)
        if request is None:
            raise AccessRefused("grant request evidence is missing")
        request.status = "closed"
        _event(db, request, "expired", as_of, grant_id=grant.id)
    db.flush()
    return len(rows)


__all__ = [
    "AccessRefused",
    "BREAK_GLASS_MAX_TTL",
    "CONSENT_MAX_TTL",
    "admit_request",
    "create_request",
    "expire_grants",
    "revoke_grant",
]
