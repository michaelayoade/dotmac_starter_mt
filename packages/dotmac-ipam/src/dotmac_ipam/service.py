"""Public IPAM command and query surface (flush-only; the caller owns the TX).

Commands take ``(db, *, tenant_id, command)`` and run inside the module write
scope; queries are read-only. Implementation lives in topic modules:
``pools``, ``allocator``, ``repair``, ``prefixes``, ``queries``.
"""

from __future__ import annotations

from dotmac_ipam.allocator import (
    allocate_address,
    allocate_management_address,
    assign_address,
    mark_primary,
    move_assignment,
    release_assignment,
    release_for_subject,
)
from dotmac_ipam.errors import (
    IpamConflict,
    IpamError,
    IpamNotFound,
    PoolExhausted,
    StalePreview,
    UnsafeRepair,
)
from dotmac_ipam.pools import (
    add_block,
    create_pool,
    live_pool_counts,
    prune_utilization,
    record_imported,
    record_utilization,
    reserve_address,
    retire_block,
    sweep_expired,
    update_pool,
    utilization,
)
from dotmac_ipam.prefixes import (
    allocate_delegated_prefix,
    delegated_prefix_for,
    release_delegated_prefix,
    retire_routed_prefix,
    upsert_routed_prefix,
)
from dotmac_ipam.queries import (
    audit_consistency,
    check_address_available,
    events,
    lookup_address,
    lookup_assignments,
    pools_for_site,
    primary_ipv4_for,
)
from dotmac_ipam.repair import (
    apply_ipv6_link,
    apply_repair,
    apply_unlink,
    preview_ipv6_link,
    preview_repair,
    preview_unlink,
)

__all__ = [
    "IpamConflict",
    "IpamError",
    "IpamNotFound",
    "PoolExhausted",
    "StalePreview",
    "UnsafeRepair",
    "add_block",
    "allocate_address",
    "allocate_delegated_prefix",
    "allocate_management_address",
    "apply_ipv6_link",
    "apply_repair",
    "apply_unlink",
    "assign_address",
    "audit_consistency",
    "check_address_available",
    "create_pool",
    "delegated_prefix_for",
    "events",
    "live_pool_counts",
    "lookup_address",
    "lookup_assignments",
    "mark_primary",
    "move_assignment",
    "pools_for_site",
    "preview_ipv6_link",
    "preview_repair",
    "preview_unlink",
    "primary_ipv4_for",
    "prune_utilization",
    "record_imported",
    "record_utilization",
    "release_assignment",
    "release_delegated_prefix",
    "release_for_subject",
    "reserve_address",
    "retire_block",
    "retire_routed_prefix",
    "sweep_expired",
    "update_pool",
    "upsert_routed_prefix",
    "utilization",
]
