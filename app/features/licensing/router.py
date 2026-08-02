"""Licence receiver endpoint — a thin adapter around `service.apply_licence`.

The route validates, authorizes (tenant admin), delegates, and audits; the
verification/projection decisions live in the service. A rejected licence is a
200 with `applied=false` + the rejection ack — the caller (vendor delivery
transport) needs the acknowledgement either way, not an HTTP error shape.
"""

from __future__ import annotations

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.deps import get_db, require_role, require_tenant
from dotmac_kernel.models import Party, Tenant
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.features.licensing import revocation as licensing_revocation
from app.features.licensing import service as licensing_service
from app.features.licensing.schemas import (
    AcknowledgementOut,
    ApplyLicenceRequest,
    ApplyLicenceResponse,
    ImportRevocationListRequest,
    ImportRevocationListResponse,
)

router = APIRouter(
    prefix="/licences", tags=["licensing"], dependencies=[Depends(require_tenant)]
)


@router.post("/apply", response_model=ApplyLicenceResponse)
def apply_licence(
    payload: ApplyLicenceRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_role("admin")),
) -> ApplyLicenceResponse:
    result = licensing_service.apply_licence(
        db, tenant_id=tenant.id, envelope=payload.envelope
    )
    ack = result.acknowledgement
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        action="licence.applied" if result.applied else "licence.rejected",
        entity_type="licence",
        entity_id=ack.licence_id,
        details={
            "licence_version": ack.licence_version,
            "digest": ack.digest,
            "status": ack.status,
            "reason": ack.reason,
            "granted_codes": list(result.granted_codes),
            "revoked_codes": list(result.revoked_codes),
        },
    )
    return ApplyLicenceResponse(
        acknowledgement=AcknowledgementOut(
            licence_id=ack.licence_id,
            licence_version=ack.licence_version,
            digest=ack.digest,
            status=ack.status,
            reason=ack.reason,
            deployment_id=ack.deployment_id,
        ),
        applied=result.applied,
        granted_codes=list(result.granted_codes),
        revoked_codes=list(result.revoked_codes),
        validity=result.validity,
        reapplied=result.reapplied,
    )


@router.post("/revocations/import", response_model=ImportRevocationListResponse)
def import_revocation_list(
    payload: ImportRevocationListRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_role("admin")),
) -> ImportRevocationListResponse:
    """Import a signed revocation list. Accepted imports revoke matching local
    grants IMMEDIATELY — deferring to the next licence application would leave
    a revoked customer entitled indefinitely."""
    result = licensing_revocation.import_revocation_list(
        db, tenant_id=tenant.id, envelope=payload.envelope
    )
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        action=(
            "licence.revocation_imported"
            if result.accepted
            else "licence.revocation_rejected"
        ),
        entity_type="licence_revocation_list",
        entity_id=str(result.list_version or "none"),
        details={
            "accepted": result.accepted,
            "list_version": result.list_version,
            "revoked_licence_ids": list(result.revoked_licence_ids),
            "revoked_codes": list(result.revoked_codes),
            "reason": result.reason,
        },
    )
    return ImportRevocationListResponse(
        accepted=result.accepted,
        list_version=result.list_version,
        revoked_licence_ids=list(result.revoked_licence_ids),
        revoked_codes=list(result.revoked_codes),
        reason=result.reason,
    )
