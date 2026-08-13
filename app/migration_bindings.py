"""This assembly's answers to "which revision supplies that effect?".

A module lineage declares the database effects it needs
(`ModuleManifest.requires`, `dotmac_kernel.prerequisites`). It never names a
foreign revision, because the answer differs per assembly. This file is where
THIS assembly answers. It is the same decision as `alembic.ini`'s
`version_locations` — which lineages are composed, and which of their revisions
supply the shared effects — and lives in the assembly package rather than beside
`alembic.ini` only because `alembic` is the installed distribution's name.

The reference assembly runs the kernel lineage, so both answers are kernel
`0001`. That is not the interesting case — the point of the indirection is that
ERP, which hosts `public.tenants` in its own lineage and structurally cannot run
kernel `0001`, writes a different file here and installs the same modules.

Binding is not belief: `require_prerequisites` re-proves each effect against the
live catalog before the requiring migration runs, and the order canary requires
the named revision to be present in `alembic_version`. A wrong entry here fails
at `alembic upgrade`, before any DDL.
"""

from __future__ import annotations

from typing import Final

from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
    PrerequisiteBinding,
)

#: Kernel `0001` creates `tenants`/`tenant_domains` and `app_current_tenant_id()`
#: (`_create_tenants_table`, `_create_current_tenant_function`) and the three
#: database roles (`_ensure_roles`). It supplies far more than this — people,
#: credentials, sessions, RBAC, audit — which is exactly why a module requiring
#: a foreign-key target declares the effect rather than the revision.
ASSEMBLY_PREREQUISITE_BINDINGS: Final[tuple[PrerequisiteBinding, ...]] = (
    PrerequisiteBinding(
        prerequisite=TENANT_SCOPE_CATALOG_V1.name,
        provider_revision="0001_initial_tenant_schema",
        provider_owner="kernel",
    ),
    PrerequisiteBinding(
        prerequisite=MODULE_DATABASE_ROLES_V1.name,
        provider_revision="0001_initial_tenant_schema",
        provider_owner="kernel",
    ),
)

__all__ = ["ASSEMBLY_PREREQUISITE_BINDINGS"]
