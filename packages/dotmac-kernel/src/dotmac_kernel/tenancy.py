"""Which tenant, if any, this deployment is bound to.

ADR-0003 makes "dedicated one-tenant deployment per ISP" the safe default
topology, and until now nothing enforced it. A deployment that acquired rows for
a second tenant — a restored backup, a migration rehearsal, a shared database
someone meant to split — would serve them to anyone who knew the host, with no
error state, because from the resolver's point of view the host resolved fine.

Two decisions worth stating, because both are easy to get backwards:

**The deployment declares the mode, not the identity.** `TENANCY=single` says
"exactly one tenant lives here"; it does not name which. The tenant already
exists as a row, and naming it in configuration too would be a second source of
truth that can drift from the first — with a typo taking the deployment down for
no reason at all. The binding below is discovered from the database at startup,
so it cannot disagree with it.

**The assertion belongs at startup, not per request.** Refusing a wrong host is
a symptom-level control that only fires if somebody tries. Refusing to *boot*
with two tenant rows catches the hazard itself, at deploy time, whether or not
anyone probes for it. The per-request check in `TenantResolverMiddleware` is the
second half only — it covers a tenant created after startup, which no assertion
would see until the next restart.
"""

from __future__ import annotations

import threading

__all__ = ["bind_single_tenant", "clear_single_tenant_binding", "single_tenant_binding"]

_lock = threading.Lock()
_bound_slug: str | None = None


def bind_single_tenant(slug: str) -> None:
    """Record the one tenant this deployment serves. Called by the startup check.

    Lowercased on the way in: hosts are case-insensitive, so a binding compared
    against one must be too, or the control fails open on a capitalised slug.
    """
    global _bound_slug
    with _lock:
        _bound_slug = slug.strip().lower()


def single_tenant_binding() -> str | None:
    """The bound slug, or None when this deployment is multi-tenant.

    None is the historical behaviour and the default: nothing is bound unless a
    deployment declared `TENANCY=single` and the startup check passed.
    """
    return _bound_slug


def clear_single_tenant_binding() -> None:
    """Drop the binding. For tests, and for an app rebuilt in one process."""
    global _bound_slug
    with _lock:
        _bound_slug = None
