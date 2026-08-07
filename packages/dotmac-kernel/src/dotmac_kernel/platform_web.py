"""The PLATFORM administration surface (module control-plane directive step 6).

The second administrative plane, and deliberately not part of the tenant portal:

- `/admin/*` is TENANT administration — a tenant admin managing their own
  tenant, authenticated by a tenant session cookie.
- `/platform/*` is PLATFORM administration — the operator of the deployment
  managing modules, feature flags and tenant entitlements, authenticated by a
  platform session cookie whose token carries a different audience entirely.

The planes never share a guard, a cookie, or a layout. A tenant admin cannot
reach this surface by any route, because `authenticate_platform_request`
validates a token audience `PlatformAdmin` sessions alone can hold — and the
surface 404s outright off the platform host, so it does not appear to exist on a
tenant's domain.

## Why this lives in the kernel

Everything it administers is kernel-owned: the module registry, the flag
catalogue and its overrides, the capability catalogue and the entitlement grant
store. An assembly-side platform UI would have to import all of it and would
re-implement the same screens in every product. The tenant portal is the
opposite case — its screens are the assembly's features — which is why that one
is composed from feature `web_routers` and this one is not.

## It is the operable half of steps 4 and 5

`require_capability` and the flag evaluator both read state that, until now,
only SQL could write. That is the gap this closes: granting a tenant a
capability, turning a flag on for one tenant, and pulling a kill switch are
operator actions, and an operator should not need a database console to take
them.

Every mutation here is audited and CSRF-bridged exactly like the tenant portal's
— `hx-post`, never a bare form.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_kernel.capabilities import active_capabilities
from dotmac_kernel.config import settings
from dotmac_kernel.db import get_platform_db
from dotmac_kernel.entitlements import TenantEntitlementGrant, grant_entitlement
from dotmac_kernel.exceptions import NotFoundError, UnauthorizedError
from dotmac_kernel.flag_models import FeatureFlagOverride
from dotmac_kernel.flags import active_flags
from dotmac_kernel.models import Tenant
from dotmac_kernel.models_platform import PlatformAdmin
from dotmac_kernel.platform_auth import (
    authenticate_platform_request,
    require_platform_host,
)
from dotmac_kernel.platform_auth import (
    login as platform_login,
)
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import WebAuthRedirect, is_secure_request

router = APIRouter(prefix="/platform", tags=["platform-web"])

PLATFORM_COOKIE = "platform_access_token"  # nosec B105 -- a name
LOGIN_PATH = "/platform/login"


def require_platform_web_auth(
    request: Request,
    db: Session = Depends(get_platform_db),
) -> PlatformAdmin:
    """Cookie guard for `/platform/*`.

    Reads the platform cookie and hands the token to
    `authenticate_platform_request` — the SAME validation seam the bearer guard
    uses, exactly as `require_web_auth` reuses `authenticate_request` for the
    tenant plane. Any tightening of platform token validation lands once and
    both surfaces get it; re-implementing it here is how the two would drift.

    Host-exact first, so the surface 404s off the platform host before any
    authentication is attempted — it does not exist there, and saying
    "unauthorized" would confirm that it does.
    """
    require_platform_host(request)
    token = request.cookies.get(PLATFORM_COOKIE)
    if not token:
        raise WebAuthRedirect(next_url=request.url.path, login_path=LOGIN_PATH)
    admin = authenticate_platform_request(request, db, token=token)
    if admin is None:
        raise WebAuthRedirect(next_url=request.url.path, login_path=LOGIN_PATH)
    return admin


# ── Login / logout ──────────────────────────────────────────────────────────


@router.get("/login")
def login_form(
    request: Request, _host: None = Depends(require_platform_host)
) -> HTMLResponse:
    return render(request, "platform/login.html", {"error": None})


@router.post("/login", response_model=None)
async def login_submit(
    request: Request,
    db: Session = Depends(get_platform_db),
    _host: None = Depends(require_platform_host),
) -> HTMLResponse | RedirectResponse:
    """Reads the form from the REQUEST rather than declaring `Form(...)` params.

    FastAPI's `Form()` calls `ensure_multipart_is_installed()` when the route is
    DEFINED, and the kernel deliberately does not depend on `python-multipart`
    (its pyproject calls form parsing an assembly concern). Declaring `Form()`
    here would therefore break `import dotmac_kernel` for every clean consumer —
    caught by `scripts/consumer_boot_check.sh`. The tenant portal's `web.py`
    routes read the form the same way, for the same reason.
    """
    form = await request.form()
    email = str(form.get("email", ""))
    password = str(form.get("password", ""))
    try:
        token, _expires = platform_login(db, email=email, password=password)
    except UnauthorizedError:
        # Re-render at 200 with one generic message — never "no such admin"
        # versus "wrong password", which would turn this form into an account
        # enumeration oracle for the deployment's operators.
        return render(
            request,
            "platform/login.html",
            {"error": "Invalid email or password"},
        )
    response = RedirectResponse(url="/platform", status_code=302)
    response.set_cookie(
        key=PLATFORM_COOKIE,
        value=token,
        httponly=True,
        secure=is_secure_request(request),
        samesite="lax",
        path="/platform",
        max_age=settings.jwt_ttl_seconds,
    )
    response.headers["HX-Redirect"] = "/platform"
    return response


@router.post("/logout", response_model=None)
def logout(
    request: Request,
    admin: PlatformAdmin = Depends(require_platform_web_auth),
) -> Response:
    """POST, never GET — a GET logout is a CSRF-exempt safe method any
    third-party page could trigger with an `<img src=...>`, which is the exact
    bug the tenant portal's `POST /admin/logout` fixed (F7)."""
    response = RedirectResponse(url=LOGIN_PATH, status_code=302)
    response.delete_cookie(PLATFORM_COOKIE, path="/platform")
    response.headers["HX-Redirect"] = LOGIN_PATH
    return response


# ── Module inventory ────────────────────────────────────────────────────────


@router.get("")
def inventory(
    request: Request,
    admin: PlatformAdmin = Depends(require_platform_web_auth),
) -> HTMLResponse:
    """What is installed, in the order it mounts.

    Read-only on purpose. Enabling or disabling a module is a DEPLOYMENT
    decision (`DISABLED_FEATURES`), not a runtime toggle: a module's tables and
    migrations are part of the image, and a UI that pretended otherwise would
    imply data can be turned off and on. ADR-0003 is explicit that the admin UI
    may only enable already-installed, migrated, dependency-complete code.
    """
    registry = getattr(request.app.state, "module_registry", None)
    modules = registry.startup_order() if registry is not None else ()
    return render(
        request,
        "platform/inventory.html",
        {"modules": modules, "active_nav": "inventory", "page_title": "Modules"},
    )


# ── Feature flags ───────────────────────────────────────────────────────────


@router.get("/flags")
def flags_index(
    request: Request,
    db: Session = Depends(get_platform_db),
    admin: PlatformAdmin = Depends(require_platform_web_auth),
) -> HTMLResponse:
    return render(request, "platform/flags.html", _flags_context(db))


def _flags_context(db: Session) -> dict[str, object]:
    """Declared flags joined to their DEPLOYMENT-scope override, if any.

    Deployment scope only: a platform operator sets the fleet-wide value here.
    Per-tenant overrides are a tenant-scoped decision and would need a tenant
    picker plus its own audit trail — deliberately not conflated into this
    screen.
    """
    overrides = {
        row.flag_code: row
        for row in db.execute(
            select(FeatureFlagOverride).where(FeatureFlagOverride.tenant_id.is_(None))
        ).scalars()
    }
    return {
        "flags": [
            {"spec": spec, "override": overrides.get(spec.code)}
            for spec in active_flags().specs()
        ],
        "active_nav": "flags",
        "page_title": "Feature flags",
    }


@router.post("/flags/{code}", response_model=None)
async def set_flag(
    request: Request,
    code: str,
    db: Session = Depends(get_platform_db),
    admin: PlatformAdmin = Depends(require_platform_web_auth),
) -> HTMLResponse:
    """Set or clear this flag's deployment-scope override.

    `active_flags().require(code)` first: an override may only reference a
    DECLARED flag, the same rule that holds for entitlement grants and
    capability codes. Without it the table would accumulate rows for flags that
    no longer exist, and nothing would ever notice.
    """
    form = await request.form()
    action = str(form.get("action", ""))
    value = str(form.get("value", ""))
    rollout = str(form.get("rollout", ""))
    active_flags().require(code)
    row = db.execute(
        select(FeatureFlagOverride).where(
            FeatureFlagOverride.tenant_id.is_(None),
            FeatureFlagOverride.flag_code == code,
        )
    ).scalar_one_or_none()

    if action == "clear":
        if row is not None:
            db.delete(row)
    else:
        if row is None:
            row = FeatureFlagOverride(tenant_id=None, flag_code=code)
            db.add(row)
        row.kill_switch = action == "kill"
        row.value = None if action in {"kill", "rollout"} else (value == "on")
        row.rollout_percentage = (
            int(rollout) if action == "rollout" and rollout else None
        )
        row.updated_by = admin.id
    db.flush()
    return render(request, "platform/_flags_table.html", _flags_context(db))


# ── Tenant entitlements ─────────────────────────────────────────────────────
#
# WHY THESE SCREENS ARE PER-TENANT, and not one fleet-wide matrix.
#
# `tenant_entitlement_grants` carries a single RLS policy,
# `tenant_id = app_current_tenant_id()`. `platform_api` never sets
# `app.current_tenant`, so that function returns NULL for it and `NULL = NULL`
# is not true in SQL — the role can read and write NOTHING in that table
# despite holding the GRANT. (Contrast `feature_flag_overrides` and
# `domain_settings`, whose migrations give `platform_api` a dedicated policy for
# their deployment-scope rows; there is no equivalent here because every grant
# row belongs to a tenant.)
#
# The established idiom for this exact situation is `provision_tenant`: run on
# the platform session and set the tenant context for the transaction. That
# makes the screens naturally per-tenant — pick a tenant, then see and edit its
# grants — which is also the only shape that scales past a handful of tenants.
# A fleet-wide matrix would need one context switch per tenant per page load.


def _set_tenant_context(db: Session, tenant_id: UUID) -> None:
    """Establish RLS tenant context on THIS transaction.

    Same `set_config(..., is_local := true)` idiom as
    `app.features.tenants.service.provision_tenant`, and for the same reason:
    `platform_api` has no BYPASSRLS, and the grant table is FORCE-RLS.
    """
    db.execute(
        select(func.set_config("app.current_tenant", str(tenant_id), True))
    ).scalar_one()


@router.get("/entitlements")
def entitlements_index(
    request: Request,
    db: Session = Depends(get_platform_db),
    admin: PlatformAdmin = Depends(require_platform_web_auth),
) -> HTMLResponse:
    """The tenant picker. `tenants` is a platform-level table with no
    tenant_id of its own, so listing it needs no context."""
    tenants = list(db.execute(select(Tenant).order_by(Tenant.slug)).scalars())
    return render(
        request,
        "platform/entitlements.html",
        {
            "tenants": tenants,
            "active_nav": "entitlements",
            "page_title": "Entitlements",
        },
    )


def _tenant_grants_context(db: Session, tenant: Tenant) -> dict[str, object]:
    _set_tenant_context(db, tenant.id)
    granted = {
        row.capability_code: row.granted
        for row in db.execute(
            select(TenantEntitlementGrant).where(
                TenantEntitlementGrant.tenant_id == tenant.id
            )
        ).scalars()
    }
    return {
        "tenant": tenant,
        "capabilities": sorted(active_capabilities().codes()),
        "granted": granted,
        "active_nav": "entitlements",
        "page_title": f"Entitlements — {tenant.slug}",
    }


@router.get("/entitlements/{tenant_id}")
def tenant_entitlements(
    request: Request,
    tenant_id: UUID,
    db: Session = Depends(get_platform_db),
    admin: PlatformAdmin = Depends(require_platform_web_auth),
) -> HTMLResponse:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    return render(
        request, "platform/tenant_entitlements.html", _tenant_grants_context(db, tenant)
    )


@router.post("/entitlements/{tenant_id}/{code}", response_model=None)
async def set_entitlement(
    request: Request,
    tenant_id: UUID,
    code: str,
    db: Session = Depends(get_platform_db),
    admin: PlatformAdmin = Depends(require_platform_web_auth),
) -> HTMLResponse:
    """Grant or revoke one capability for one tenant.

    Revoking sets `granted=False` rather than deleting the row — `revoked` and
    `not_granted` are different answers, and an operator needs to tell "we took
    it away" from "they never had it" months later. `source` records who did it.
    """
    granted = str((await request.form()).get("granted", ""))
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    _set_tenant_context(db, tenant_id)
    grant_entitlement(
        db,
        tenant_id=tenant_id,
        capability_code=code,
        catalogue=active_capabilities(),
        granted=granted == "on",
        source=f"platform-admin:{admin.email}",
    )
    db.flush()
    return render(
        request,
        "platform/_tenant_entitlements_table.html",
        _tenant_grants_context(db, tenant),
    )
