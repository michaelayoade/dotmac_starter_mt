"""Mapped classes for same-database product READERS (ruled N4, 2026-10-26).

This widens "snapshots, never ORM instances" for same-database readers only:
Sub may ``select(...)`` these classes but must never ``add``/mutate/delete
them. The ``before_flush`` guard in :mod:`dotmac_ipam.guard` turns that into a
runtime refusal for any write made outside a module command scope. The reader
inventory is a counted, shrink-only list kept by the adopting product.
"""

from dotmac_ipam.models import (
    IpamEvent,
    IPAssignment,
    IpBlock,
    IpPool,
    IpPoolUtilizationSnapshot,
    IPv4Address,
    IPv6Address,
    Ipv6DelegatedPrefix,
    SubscriberAdditionalRoute,
)

__all__ = [
    "IPAssignment",
    "IPv4Address",
    "IPv6Address",
    "IpBlock",
    "IpPool",
    "IpPoolUtilizationSnapshot",
    "IpamEvent",
    "Ipv6DelegatedPrefix",
    "SubscriberAdditionalRoute",
]
