"""Settings-admin's `ModuleManifest` (module contract 2), STATELESS.

No `short_code`, `migration_*` or `tables`: this module owns no schema. It
administers the kernel's `domain_settings` / `domain_setting_history` rows and
writes kernel audit events, so it needs no migration-namespace allocation.

It declares and owns: the staff surface (`staff` on `staff_admin`), three
permissions, the audit vocabulary, and the setting domains `branding`,
`display`, `organisation` and `settings_admin` (see `specs`).
"""

from __future__ import annotations

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.permissions import PermissionSpec
from dotmac_kernel.web_surfaces import (
    BrowserCapabilityRequirement,
    LocalizedText,
    TemplatePackage,
    WebNavItem,
    WebSurfaceContribution,
)

from dotmac_settings_admin import specs as _specs  # registers the specs
from dotmac_settings_admin.router import router as api_router
from dotmac_settings_admin.service import AUDIT_ACTIONS
from dotmac_settings_admin.web import router as web_router
from dotmac_settings_admin.web import template_dir

module = ModuleManifest(
    code="settings_admin",
    version="0.1.0a1",
    api_routers=[api_router],
    web_surfaces=(
        WebSurfaceContribution(
            code="staff",
            facet="staff_admin",
            routers=(web_router,),
            navigation=(
                WebNavItem(
                    code="settings_admin.settings",
                    region="primary",
                    label=LocalizedText("settings_admin.settings.nav", "Settings"),
                    route_name="index",
                    order=90,
                ),
            ),
            templates=TemplatePackage(namespace="settings_admin", root=template_dir()),
            supported_ui_contract_versions=frozenset({1}),
            browser_capabilities=(BrowserCapabilityRequirement("htmx", 2),),
        ),
    ),
    setting_domains=_specs.OWNED_DOMAINS,
    permissions=[
        PermissionSpec(
            code="settings_admin.read",
            description="View declared settings, their history, flags and exports.",
        ),
        PermissionSpec(
            code="settings_admin.manage",
            description=(
                "Change, clear, restore and import settings. Every change needs a "
                "reason and is audited."
            ),
        ),
        PermissionSpec(
            code="settings_admin.approve",
            description=(
                "Apply or reject proposed changes when the optional maker-checker "
                "is on; also lets the holder write directly while it is on."
            ),
        ),
    ],
    audit_actions=list(AUDIT_ACTIONS),
    core=False,
)

__all__ = ["module"]
