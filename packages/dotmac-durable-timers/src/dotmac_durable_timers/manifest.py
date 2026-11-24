"""Selectable dual-plane manifest for durable timer mechanics and the job runner.

Contract 2. Beyond the timer mechanics it declares the job runner's surface:
the ``durable_timers.job_due.v1`` event type this module consumes, its own
built-in retention jobs, the staff permissions and audit vocabulary, and the
staff API and page. It declares and reuses ``outbox_relay.v1`` for every claim,
lease, retry and dead-letter; it adds none of its own.
"""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.permissions import PermissionSpec
from dotmac_kernel.planes import ModulePlane
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    TENANT_AUDIT_LOG_V1,
    TENANT_SCOPE_CATALOG_V1,
)
from dotmac_kernel.web_surfaces import (
    BrowserCapabilityRequirement,
    LocalizedText,
    TemplatePackage,
    WebNavItem,
    WebSurfaceContribution,
)

from dotmac_durable_timers.builtin import BUILTIN_JOBS
from dotmac_durable_timers.jobs import JOB_DUE_EVENT
from dotmac_durable_timers.models import PLATFORM_TABLES, TENANT_TABLES
from dotmac_durable_timers.router import router as api_router
from dotmac_durable_timers.web import router as web_router
from dotmac_durable_timers.web import template_dir

module = ModuleManifest(
    code="durable_timers",
    version="0.1.0a3",
    contract_version=2,
    core=False,
    short_code="timers",
    migration_prefix="dt",
    migration_branch="durable_timers",
    tables=TENANT_TABLES,
    platform_tables=PLATFORM_TABLES,
    requires=(MODULE_DATABASE_ROLES_V1.name, OUTBOX_RELAY_V1.name),
    tenant_requires=(TENANT_SCOPE_CATALOG_V1.name, TENANT_AUDIT_LOG_V1.name),
    supported_plane_sets=(
        (ModulePlane.TENANT,),
        (ModulePlane.PLATFORM,),
        (ModulePlane.TENANT, ModulePlane.PLATFORM),
    ),
    outbox_event_types=(JOB_DUE_EVENT,),
    jobs=BUILTIN_JOBS,
    api_routers=[api_router],
    web_surfaces=(
        WebSurfaceContribution(
            code="staff",
            facet="staff_admin",
            routers=(web_router,),
            navigation=(
                WebNavItem(
                    code="durable_timers.jobs",
                    region="primary",
                    label=LocalizedText("durable_timers.jobs.nav", "Background jobs"),
                    route_name="list",
                    order=90,
                ),
            ),
            templates=TemplatePackage(namespace="durable_timers", root=template_dir()),
            supported_ui_contract_versions=frozenset({1}),
            browser_capabilities=(BrowserCapabilityRequirement("htmx", 2),),
        ),
    ),
    permissions=[
        PermissionSpec(
            code="durable_timers.jobs.read",
            description="List declared jobs, next due, last outcome and dead letters.",
        ),
        PermissionSpec(
            code="durable_timers.jobs.run",
            description="Run a job now (makes its next generation due immediately).",
        ),
        PermissionSpec(
            code="durable_timers.jobs.manage",
            description="Pause and resume a job's scheduling.",
        ),
    ],
    audit_actions=[
        "durable_timers.job.run_now",
        "durable_timers.job.pause",
        "durable_timers.job.resume",
        "durable_timers.job.dead_letter",
    ],
)

__all__ = ["module"]
