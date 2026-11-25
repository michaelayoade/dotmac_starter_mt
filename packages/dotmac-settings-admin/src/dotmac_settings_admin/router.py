"""Settings-admin JSON API: a thin adapter over `service.py` and `transfer.py`.

Authorization names the DECISION as a permission this module's manifest owns
(`require_permission`), never a role. Reads need `settings_admin.read`; every
write needs `settings_admin.manage` plus a reason; `settings_admin.approve`
matters only when the optional maker-checker is on.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import UUID

from dotmac_kernel.deps import (
    authorize_party,
    get_db,
    require_permission,
    require_tenant,
)
from dotmac_kernel.models import Party, Tenant
from dotmac_kernel.request_evidence import current_evidence
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from dotmac_settings_admin import service, transfer
from dotmac_settings_admin.schemas import (
    DomainOut,
    HistoryOut,
    ImportApply,
    ImportRequest,
    PlanEntryOut,
    PlanOut,
    ReasonOnly,
    SettingOut,
    SettingUpdate,
    WriteResult,
)

router = APIRouter(
    prefix="/settings", tags=["settings"], dependencies=[Depends(require_tenant)]
)

READ = "settings_admin.read"
MANAGE = "settings_admin.manage"
APPROVE = "settings_admin.approve"


def actor_from(request: Request, party: Party) -> service.Actor:
    # The kernel's request evidence resolves the client address under the
    # assembly's trusted-proxy policy; fall back to the socket peer only when
    # no evidence context was created (a bare app).
    evidence = current_evidence()
    client = evidence.client_address or (
        request.client.host if request.client else None
    )
    return service.Actor(
        party_id=party.id,
        ip_address=client or None,
        user_agent=request.headers.get("user-agent"),
        request_id=evidence.request_id or getattr(request.state, "request_id", None),
    )


def may_approve(db: Session, tenant: Tenant, party: Party) -> bool:
    return authorize_party(db, tenant=tenant, party=party, code=APPROVE)


def _asdict(obj: Any) -> dict[str, Any]:
    result: dict[str, Any] = asdict(obj)
    return result


def _out(view: service.SettingView) -> SettingOut:
    return SettingOut(**_asdict(view))


def plan_out(plan: transfer.ImportPlan) -> PlanOut:
    return PlanOut(
        ok=plan.ok,
        digest=plan.digest,
        entries=[PlanEntryOut(**_asdict(e)) for e in plan.entries],
    )


@router.get("", response_model=list[DomainOut])
def list_domains(
    db: Session = Depends(get_db), _: Party = Depends(require_permission(READ))
) -> list[DomainOut]:
    return [DomainOut(**_asdict(d)) for d in service.list_domains(db)]


# Fixed paths are declared before `/{domain}` so they are never read as a domain.
@router.get("/export")
def export_settings(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission(READ)),
) -> dict[str, Any]:
    return transfer.export_settings(db, tenant.id)


@router.post("/import/preview", response_model=PlanOut)
def import_preview(
    payload: ImportRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission(MANAGE)),
) -> PlanOut:
    return plan_out(transfer.plan_import(db, tenant.id, payload.document))


@router.post("/import", response_model=dict[str, Any])
def import_apply(
    payload: ImportApply,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_permission(MANAGE)),
) -> dict[str, Any]:
    changed = transfer.apply_import(
        db,
        tenant.id,
        actor=actor_from(request, party),
        raw=payload.document,
        reason=payload.reason,
        expected_digest=payload.digest,
        may_approve=may_approve(db, tenant, party),
    )
    return {"changed": changed}


@router.post("/history/{history_id}/restore", response_model=WriteResult)
def restore(
    history_id: UUID,
    payload: ReasonOnly,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_permission(MANAGE)),
) -> WriteResult:
    outcome = service.restore_setting(
        db,
        tenant.id,
        actor=actor_from(request, party),
        history_id=history_id,
        reason=payload.reason,
        may_approve=may_approve(db, tenant, party),
    )
    return WriteResult(
        applied=outcome.applied, proposal_id=None, setting=_out(outcome.setting)
    )


@router.get("/{domain}", response_model=list[SettingOut])
def list_settings(
    domain: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission(READ)),
) -> list[SettingOut]:
    return [_out(v) for v in service.list_settings(db, tenant.id, domain)]


@router.put("/{domain}/{key}", response_model=WriteResult)
def update_setting(
    domain: str,
    key: str,
    payload: SettingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_permission(MANAGE)),
) -> WriteResult:
    outcome = service.write_setting(
        db,
        tenant.id,
        actor=actor_from(request, party),
        domain=domain,
        key=key,
        value=payload.value,
        reason=payload.reason,
        may_approve=may_approve(db, tenant, party),
    )
    return WriteResult(
        applied=outcome.applied,
        proposal_id=outcome.proposal_id,
        setting=_out(outcome.setting),
    )


@router.delete("/{domain}/{key}", response_model=SettingOut)
def clear_setting(
    domain: str,
    key: str,
    payload: ReasonOnly,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_permission(MANAGE)),
) -> SettingOut:
    return _out(
        service.clear_setting(
            db,
            tenant.id,
            actor=actor_from(request, party),
            domain=domain,
            key=key,
            reason=payload.reason,
            may_approve=may_approve(db, tenant, party),
        )
    )


@router.get("/{domain}/{key}/history", response_model=list[HistoryOut])
def history(
    domain: str,
    key: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission(READ)),
) -> list[HistoryOut]:
    return [
        HistoryOut(**_asdict(h))
        for h in service.list_history(db, tenant.id, domain, key)
    ]


@router.post("/proposals/{proposal_id}/approve", response_model=SettingOut)
def approve(
    proposal_id: str,
    payload: ReasonOnly,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_permission(APPROVE)),
) -> SettingOut:
    return _out(
        service.approve_proposal(
            db,
            tenant.id,
            actor=actor_from(request, party),
            proposal_id=proposal_id,
            reason=payload.reason,
        )
    )


@router.post("/proposals/{proposal_id}/reject", response_model=dict[str, Any])
def reject(
    proposal_id: str,
    payload: ReasonOnly,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_permission(APPROVE)),
) -> dict[str, Any]:
    service.reject_proposal(
        db,
        tenant.id,
        actor=actor_from(request, party),
        proposal_id=proposal_id,
        reason=payload.reason,
    )
    return {"rejected": proposal_id}
