"""Public Network Topology service surface."""

from dotmac_network_topology.manifest import module
from dotmac_network_topology.migrations import versions_dir
from dotmac_network_topology.service import (
    TopologyConflict,
    TopologyError,
    TopologyNotFound,
    declare_link,
    lookup_links,
    query_coverage,
    query_paths,
    query_reachability,
    rebuild_topology,
    record_observed_link,
    resolve_forwarding,
    withdraw_link,
)

__version__ = "0.1.0a1"
__all__ = [
    "__version__",
    "TopologyConflict",
    "TopologyError",
    "TopologyNotFound",
    "declare_link",
    "lookup_links",
    "module",
    "query_coverage",
    "query_paths",
    "query_reachability",
    "rebuild_topology",
    "record_observed_link",
    "resolve_forwarding",
    "versions_dir",
    "withdraw_link",
]
