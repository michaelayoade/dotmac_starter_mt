"""Reference assembly dependency probe; no product/provider health authority."""

from __future__ import annotations

from dotmac_kernel.config import settings
from dotmac_kernel.models import Tenant
from dotmac_kernel.models_platform import PlatformAdmin
from dotmac_kernel.session_runtime import resolve_database_runtime
from dotmac_kernel.settings_resolver import validate_required_settings
from dotmac_kernel.tenancy import single_tenant_binding
from sqlalchemy import select


def readiness_errors() -> list[str]:
    """Observe both runtime roles, required settings and the declared topology.

    Adapt ERP's dependency conjunction and HTTP-503 policy through the kernel's
    existing boundaries. This never seeds settings, binds tenants, or uses
    migration credentials. Details remain internal to the readiness decision.
    """
    runtime = resolve_database_runtime()
    with runtime.readonly_session() as db:
        db.execute(select(1)).scalar_one()
        # A bare SELECT 1 would also succeed against an unmigrated database.
        db.execute(select(Tenant.id).limit(1)).first()
    with runtime.platform_session() as db:
        db.execute(select(1)).scalar_one()
        db.execute(select(PlatformAdmin.id).limit(1)).first()
        errors = validate_required_settings(db)
        if settings.tenancy == "single":
            slugs = list(db.scalars(select(Tenant.slug).limit(2)))
            if slugs != [single_tenant_binding()]:
                errors.append("single-tenant topology is not ready")
    return errors
