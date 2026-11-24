"""Request-time seams for the staff surface: the one clock read and the catalogue.

``utc_now`` is the module's single ambient clock read (everything else takes an
instant as an argument), kept in this file so the architecture canary can
allow exactly it.  It is a FastAPI dependency so a test can override it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dotmac_kernel.planes import ModulePlane
from fastapi import Request

from dotmac_durable_timers.jobs import JobCatalogue, JobEntry


def utc_now() -> datetime:
    return datetime.now(UTC)


def request_catalogue(request: Request) -> JobCatalogue:
    """The enabled tenant-plane jobs of the running app, built once per app."""
    cached = getattr(request.app.state, "durable_timers_catalogue", None)
    if cached is not None:
        assert isinstance(cached, JobCatalogue)
        return cached
    registry = request.app.state.module_registry
    disabled = {e.code for e in request.app.state.module_inventory if not e.enabled}
    catalogue = JobCatalogue(
        JobEntry(code, spec)
        for code, spec in registry.jobs(disabled)
        if spec.plane == ModulePlane.TENANT
    )
    request.app.state.durable_timers_catalogue = catalogue
    return catalogue


__all__ = ["request_catalogue", "utc_now"]
