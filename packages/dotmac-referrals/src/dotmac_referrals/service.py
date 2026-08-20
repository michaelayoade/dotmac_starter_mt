"""The single writer for referral programme and attribution lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging import enqueue_event
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_referrals.contracts import (
    CaptureReferral,
    ContractError,
    CreateProgramme,
    IssueCode,
    RecordConversion,
    fingerprint,
)
from dotmac_referrals.models import (
    Referral,
    ReferralCode,
    ReferralConversion,
    ReferralProgramme,
    ReferralProgrammeVersion,
)

REWARD_REQUESTED_EVENT = "referrals.reward.requested.v1"


class ReferralError(ValueError):
    """A referral command cannot be admitted."""


class NotFound(ReferralError):
    """A tenant-local referral subject does not exist."""


class Conflict(ReferralError):
    """A stable referral identity is already used differently."""


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{name} must be timezone-aware")


def _instant(value: datetime) -> datetime:
    """Normalize SQLite's timezone-erasing round trip for comparisons."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def create_programme(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateProgramme,
    recorded_at: datetime,
) -> ReferralProgramme:
    _aware("recorded_at", recorded_at)
    existing = db.scalar(
        select(ReferralProgramme).where(
            ReferralProgramme.tenant_id == scope.tenant_id,
            ReferralProgramme.code == command.code,
        )
    )
    if existing is not None:
        raise Conflict(f"referral programme code {command.code!r} already exists")
    programme = ReferralProgramme(
        tenant_id=scope.tenant_id,
        code=command.code,
        name=command.name,
        qualification_policy_ref=command.qualification_policy_ref,
        reward_policy_ref=command.reward_policy_ref,
        status="active",
        created_at=recorded_at,
        updated_at=recorded_at,
    )
    db.add(programme)
    db.flush()
    version = ReferralProgrammeVersion(
        tenant_id=scope.tenant_id,
        programme_id=programme.id,
        version_number=1,
        qualification_policy_ref=command.qualification_policy_ref,
        reward_policy_ref=command.reward_policy_ref,
        content_digest=fingerprint(
            {
                "programme_code": command.code,
                "qualification_policy_ref": command.qualification_policy_ref,
                "reward_policy_ref": command.reward_policy_ref,
            }
        ),
        frozen_at=recorded_at,
    )
    db.add(version)
    db.flush()
    programme.active_version_id = version.id
    db.flush()
    return programme


def issue_code(
    db: Session,
    *,
    scope: TenantScope,
    command: IssueCode,
    recorded_at: datetime,
) -> ReferralCode:
    _aware("recorded_at", recorded_at)
    if command.expires_at <= recorded_at:
        raise ReferralError("a referral code must expire after it is issued")
    programme = db.scalar(
        select(ReferralProgramme).where(
            ReferralProgramme.tenant_id == scope.tenant_id,
            ReferralProgramme.id == command.programme_id,
        )
    )
    if programme is None or programme.status != "active":
        raise NotFound("active referral programme was not found")
    existing = db.scalar(
        select(ReferralCode).where(
            ReferralCode.tenant_id == scope.tenant_id,
            ReferralCode.code == command.code,
        )
    )
    if existing is not None:
        if (
            existing.programme_id == command.programme_id
            and existing.referrer_ref == command.referrer_ref
            and existing.expires_at == command.expires_at
        ):
            return existing
        raise Conflict(
            f"referral code {command.code!r} already identifies another invitation"
        )
    row = ReferralCode(
        tenant_id=scope.tenant_id,
        programme_id=programme.id,
        referrer_ref=command.referrer_ref,
        code=command.code,
        status="active",
        expires_at=command.expires_at,
        issued_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def capture_referral(
    db: Session,
    *,
    scope: TenantScope,
    command: CaptureReferral,
    recorded_at: datetime,
) -> Referral:
    _aware("recorded_at", recorded_at)
    existing = db.scalar(
        select(Referral).where(
            Referral.tenant_id == scope.tenant_id,
            Referral.source_owner == command.source_owner,
            Referral.source_event_id == command.source_event_id,
        )
    )
    if existing is not None:
        if existing.source_fingerprint != command.source_fingerprint:
            raise Conflict(
                "a source event was replayed with different referral content"
            )
        return existing
    invitation = db.scalar(
        select(ReferralCode).where(
            ReferralCode.tenant_id == scope.tenant_id,
            ReferralCode.code == command.code,
        )
    )
    if (
        invitation is None
        or invitation.status != "active"
        or _instant(invitation.expires_at) <= _instant(recorded_at)
    ):
        raise NotFound("active referral code was not found")
    row = Referral(
        tenant_id=scope.tenant_id,
        programme_id=invitation.programme_id,
        code_id=invitation.id,
        referred_subject_ref=command.referred_subject_ref,
        source_owner=command.source_owner,
        source_event_id=command.source_event_id,
        source_fingerprint=command.source_fingerprint,
        status="attributed",
        attributed_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def record_conversion(
    db: Session,
    *,
    scope: TenantScope,
    command: RecordConversion,
    recorded_at: datetime,
) -> ReferralConversion:
    _aware("recorded_at", recorded_at)
    existing = db.scalar(
        select(ReferralConversion).where(
            ReferralConversion.tenant_id == scope.tenant_id,
            ReferralConversion.referral_id == command.referral_id,
        )
    )
    if existing is not None:
        if (
            existing.conversion_ref != command.conversion_ref
            or existing.qualification_evidence_digest
            != command.qualification_evidence_digest
        ):
            raise Conflict("a referral conversion was replayed with different evidence")
        return existing
    referral = db.scalar(
        select(Referral).where(
            Referral.tenant_id == scope.tenant_id,
            Referral.id == command.referral_id,
        )
    )
    if referral is None:
        raise NotFound("referral was not found")
    programme = db.scalar(
        select(ReferralProgramme).where(
            ReferralProgramme.tenant_id == scope.tenant_id,
            ReferralProgramme.id == referral.programme_id,
        )
    )
    if programme is None:
        raise NotFound("referral programme was not found")
    reward_request_ref = f"reward:{uuid4()}"
    placeholder_outbox_id = uuid4()
    conversion = ReferralConversion(
        tenant_id=scope.tenant_id,
        referral_id=referral.id,
        conversion_ref=command.conversion_ref,
        qualification_evidence_digest=command.qualification_evidence_digest,
        reward_request_ref=reward_request_ref,
        outbox_event_id=placeholder_outbox_id,
        converted_at=recorded_at,
    )
    db.add(conversion)
    db.flush()
    event = enqueue_event(
        db,
        tenant_id=scope.tenant_id,
        event_type=REWARD_REQUESTED_EVENT,
        correlation_id=str(conversion.id),
        payload={
            "contract": REWARD_REQUESTED_EVENT,
            "referral_id": str(referral.id),
            "referrer_ref": db.scalar(
                select(ReferralCode.referrer_ref).where(
                    ReferralCode.tenant_id == scope.tenant_id,
                    ReferralCode.id == referral.code_id,
                )
            ),
            "conversion_ref": conversion.conversion_ref,
            "reward_request_ref": reward_request_ref,
            "reward_policy_ref": programme.reward_policy_ref,
            "qualification_evidence_digest": command.qualification_evidence_digest,
        },
    )
    conversion.outbox_event_id = event.id
    referral.status = "converted"
    db.flush()
    return conversion


__all__ = [
    "Conflict",
    "NotFound",
    "REWARD_REQUESTED_EVENT",
    "ReferralError",
    "capture_referral",
    "create_programme",
    "issue_code",
    "record_conversion",
]
