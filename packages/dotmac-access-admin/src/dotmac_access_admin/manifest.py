"""`dotmac-access-admin`'s `ModuleManifest` (module contract 2, stateless).

Stateless: no `short_code`, `migration_prefix` or tables. It writes only through
the kernel's identity-storage, outbox and audit owners, inside the caller's
transaction. Permissions, audit actions and the outbox event type are declared
and OWNED here; every one has a consumer in this package.
"""

from __future__ import annotations

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.permissions import PermissionSpec
from dotmac_kernel.web_surfaces import (
    LocalizedText,
    TemplatePackage,
    WebNavItem,
    WebSurfaceContribution,
)

from dotmac_access_admin.provisioning import (
    PROVISIONED_AUDIT_ACTION,
    RECOVERY_EVENT_TYPE,
)
from dotmac_access_admin.router import router as api_router
from dotmac_access_admin.web import router as web_router
from dotmac_access_admin.web import template_dir

module = ModuleManifest(
    code="access_admin",
    version="0.1.0a1",
    api_routers=[api_router],
    web_surfaces=(
        WebSurfaceContribution(
            code="staff",
            facet="staff_admin",
            routers=(web_router,),
            navigation=(
                WebNavItem(
                    code="access_admin.users",
                    region="primary",
                    label=LocalizedText("access_admin.users.nav", "Users and access"),
                    route_name="list",
                    order=20,
                ),
            ),
            templates=TemplatePackage(namespace="access_admin", root=template_dir()),
            supported_ui_contract_versions=frozenset({1}),
        ),
    ),
    permissions=[
        PermissionSpec(
            code="access_admin.users.read",
            description="List and read the tenant's users and their roles.",
        ),
        PermissionSpec(
            code="access_admin.users.manage",
            description="Create, deactivate and reactivate users.",
        ),
        PermissionSpec(
            code="access_admin.users.revoke",
            description=(
                "Leaver revoke: remove every grant and every session of a user "
                "in one transaction."
            ),
        ),
        PermissionSpec(
            code="access_admin.roles.read",
            description="List roles, including assembly-declared read-only roles.",
        ),
        PermissionSpec(
            code="access_admin.roles.manage",
            description="Create and rename tenant roles.",
        ),
        PermissionSpec(
            code="access_admin.grants.manage",
            description="Grant a role to a user or revoke it.",
        ),
    ],
    audit_actions=[
        PROVISIONED_AUDIT_ACTION,
        "access_admin.user.create",
        "access_admin.user.deactivate",
        "access_admin.user.reactivate",
        "access_admin.user.leaver_revoke",
        "access_admin.role.create",
        "access_admin.role.rename",
        "access_admin.grant.add",
        "access_admin.grant.revoke",
    ],
    outbox_event_types=[RECOVERY_EVENT_TYPE],
    core=False,
    # No `requires`: the kernel registry refuses migration prerequisites on a
    # module that owns no lineage. The audit-log and outbox-relay effects its
    # callers need are therefore undeclared here; see PR #1160 (open decision).
)

__all__ = ["module"]
