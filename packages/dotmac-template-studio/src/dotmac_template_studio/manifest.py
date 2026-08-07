"""Template Studio's `ModuleManifest` — the first STATEFUL one (ADR-0006 M1).

Four fields make it stateful, and they must agree exactly with this module's row
in `dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER`
(`TEMPLATE_STUDIO_MIGRATION_OWNER`) or `NamespaceRegistry.from_manifests` refuses
the composition at boot:

- `short_code="tstudio"` → the derived, read-only schema `mod_tstudio`. There is
  no settable schema attribute; the namespace is never inferred from `code` or
  from any display name, because a namespace that moves is a data-loss event.
- `migration_prefix="ts"` → revision ids `ts_0001_…`, inside
  `alembic_version.version_num`'s 32-char budget.
- `migration_branch="template_studio"` → how an `alembic_version` row is
  attributed to this owner rather than to the kernel or the assembly.
- `tables=(...)` → the composed gate rejects a migration that creates anything
  outside this declaration, and the live-catalog gate checks it in BOTH
  directions after migrations run (a declared table that does not exist is as
  much a defect as an undeclared one that does).

`permissions` and `audit_actions` are declared and OWNED here: `require_permission`
fails the boot on an undeclared code, and `write_audit_event` refuses an
undeclared action at the write. Both are also checked for a real consumer, so a
code declared and never used fails the build rather than rotting.
"""

from __future__ import annotations

from dotmac_kernel.features import NavItem
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.permissions import PermissionSpec

from dotmac_template_studio.router import router as api_router
from dotmac_template_studio.web import router as web_router

module = ModuleManifest(
    code="template_studio",
    version="0.1.0a1",
    api_routers=[api_router],
    web_routers=[web_router],
    nav=[NavItem("Templates", "/admin/templates")],
    # ── D1 database identity ────────────────────────────────────────────────
    short_code="tstudio",
    migration_prefix="ts",
    migration_branch="template_studio",
    tables=("templates", "template_versions"),
    # ── Authorization ───────────────────────────────────────────────────────
    permissions=[
        PermissionSpec(
            code="template_studio.templates.read",
            description="List and read the tenant's templates and their versions.",
        ),
        PermissionSpec(
            code="template_studio.templates.manage",
            description="Create templates and author new versions of them.",
        ),
        PermissionSpec(
            code="template_studio.templates.publish",
            description=(
                "Make a version the published one — the revision every caller "
                "renders. Separate from `manage` on purpose: authoring a draft "
                "and changing what customers actually receive are different "
                "decisions, and a deployment may want to bind them to different "
                "roles."
            ),
        ),
        PermissionSpec(
            code="template_studio.templates.render",
            description="Render a template's published version.",
        ),
    ],
    # The trail's vocabulary for this module. Rendering is deliberately absent:
    # it reads state, and one audit row per outbound message would flood the
    # tenant's log with traffic rather than decisions.
    audit_actions=[
        "template_studio.template.create",
        "template_studio.template.update",
        "template_studio.template.delete",
        "template_studio.version.create",
        "template_studio.version.update",
        "template_studio.version.publish",
    ],
    # Deletable: a deployment that authors no templates should not carry the
    # module's surface. Its DATA survives disablement — ADR-0003 is explicit
    # that disabling a module preserves its data.
    core=False,
)

__all__ = ["module"]
