from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_payments.models import TENANT_TABLES

module = ModuleManifest(
    code="payments",
    version="0.1.0a3",
    core=False,
    short_code="payments",
    migration_prefix="pm",
    migration_branch="payments",
    tables=TENANT_TABLES,
    platform_tables=(),
    outbox_event_types=(
        "payments.provider_cash.qualified.v1",
        "payments.merchant_context.accepted.v1",
        "payments.merchant_context.ended.v1",
    ),
    requires=(
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
        OUTBOX_RELAY_V1.name,
    ),
)

__all__ = ["module"]
