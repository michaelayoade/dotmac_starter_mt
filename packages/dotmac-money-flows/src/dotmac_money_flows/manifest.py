"""Installable declaration for the tenant-plane money-flows composition."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.planes import ModulePlane
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_money_flows.events import EVENT_TYPES
from dotmac_money_flows.models import TENANT_TABLES

#: Every table this package may own. One table per flow; additive migrations
#: only. A table holds the common instance columns plus declared opaque
#: references matching ``^[a-z_]+_(id|ref)$`` and nothing else.
INSTANCE_TABLE_ALLOWLIST = frozenset({"settle_instances"})

module = ModuleManifest(
    code="money_flows",
    version="0.1.0a1",
    core=False,
    short_code="mflows",
    migration_prefix="mf",
    migration_branch="money_flows",
    tables=TENANT_TABLES,
    platform_tables=(),
    requires=(
        MODULE_DATABASE_ROLES_V1.name,
        OUTBOX_RELAY_V1.name,
    ),
    outbox_event_types=EVENT_TYPES,
    tenant_requires=(TENANT_SCOPE_CATALOG_V1.name,),
    supported_plane_sets=((ModulePlane.TENANT,),),
)

__all__ = ["INSTANCE_TABLE_ALLOWLIST", "module"]
