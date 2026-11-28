"""Durable timers' facet-relative staff page for declared jobs (contract 2).

Thin adapter over ``admin.py``: list, next due, last outcome, dead letters, and
the governed run-now / pause / resume commands, each audited with the actor.
Templates are package data under the ``durable_timers`` namespace.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.exceptions import ConflictError, NotFoundError
from dotmac_kernel.models import Party, Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_permission
from dotmac_kernel.web_surfaces import surface_path
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from dotmac_durable_timers import admin
from dotmac_durable_timers.jobs import JobCatalogue, JobEntry
from dotmac_durable_timers.runtime import request_catalogue, utc_now

router = APIRouter(prefix="/jobs", tags=["web"])

_PKG_DIR = Path(__file__).resolve().parent
_INDEX = "durable_timers/admin/durable_timers/index.html"


def template_dir() -> Path:
    """This package's namespaced Jinja root, resolved by package path."""
    return _PKG_DIR / "templates"


@router.get("", name="list", response_model=None)
def jobs_index(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    catalogue: JobCatalogue = Depends(request_catalogue),
    party: Party = Depends(require_web_permission("durable_timers.jobs.read")),
) -> HTMLResponse:
    jobs = admin.list_jobs(db, tenant_id=tenant.id, catalogue=catalogue)
    return render(
        request,
        _INDEX,
        {
            "active_nav": "jobs",
            "page_title": "Background jobs",
            "jobs": jobs,
            "dead_letters": admin.dead_letters(jobs),
        },
    )


def _entry(catalogue: JobCatalogue, module_code: str, job_code: str) -> JobEntry:
    entry = catalogue.get(module_code, job_code)
    if entry is None:
        raise NotFoundError("no such job")
    return entry


def _done(
    request: Request,
    db: Session,
    tenant: Tenant,
    party: Party,
    entry: JobEntry,
    action: str,
    details: dict[str, object],
) -> RedirectResponse:
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=party.id,
        actor_type="user",
        actor_id=str(party.id),
        action=action,
        entity_type="job",
        entity_id=f"{entry.module_code}:{entry.spec.code}",
        details=details,
    )
    return RedirectResponse(surface_path(request, "list"), status_code=303)


@router.post("/{module_code}/{job_code}/run", name="run", response_model=None)
def job_run(
    request: Request,
    module_code: str,
    job_code: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    catalogue: JobCatalogue = Depends(request_catalogue),
    now: datetime = Depends(utc_now),
    party: Party = Depends(require_web_permission("durable_timers.jobs.run")),
) -> RedirectResponse:
    entry = _entry(catalogue, module_code, job_code)
    try:
        scheduled = admin.run_now(db, tenant_id=tenant.id, entry=entry, now=now)
    except ConflictError:
        # Paused: nothing changed, so nothing to audit; the page shows why.
        return RedirectResponse(surface_path(request, "list"), status_code=303)
    return _done(
        request,
        db,
        tenant,
        party,
        entry,
        "durable_timers.job.run_now",
        {"generation": scheduled.generation},
    )


@router.post("/{module_code}/{job_code}/pause", name="pause", response_model=None)
def job_pause(
    request: Request,
    module_code: str,
    job_code: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    catalogue: JobCatalogue = Depends(request_catalogue),
    now: datetime = Depends(utc_now),
    party: Party = Depends(require_web_permission("durable_timers.jobs.manage")),
) -> RedirectResponse:
    entry = _entry(catalogue, module_code, job_code)
    changed = admin.pause(
        db, tenant_id=tenant.id, entry=entry, now=now, actor=str(party.id)
    )
    return _done(
        request,
        db,
        tenant,
        party,
        entry,
        "durable_timers.job.pause",
        {"changed": changed},
    )


@router.post("/{module_code}/{job_code}/resume", name="resume", response_model=None)
def job_resume(
    request: Request,
    module_code: str,
    job_code: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    catalogue: JobCatalogue = Depends(request_catalogue),
    now: datetime = Depends(utc_now),
    party: Party = Depends(require_web_permission("durable_timers.jobs.manage")),
) -> RedirectResponse:
    entry = _entry(catalogue, module_code, job_code)
    changed = admin.resume(db, tenant_id=tenant.id, entry=entry, now=now)
    return _done(
        request,
        db,
        tenant,
        party,
        entry,
        "durable_timers.job.resume",
        {"changed": changed},
    )


__all__ = ["router", "template_dir"]
