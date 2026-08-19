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
from dotmac_network_topology.models import (
    ALL_MODELS,
    SCHEMA,
)

from dotmac_network_topology.contracts import (
    DeclareLink,
    LinkKind,
    RebuildTopology,
    ResolveForwarding,
)

__all__ = [
    "__version__",
    "ALL_MODELS",
    "declare_link",
    "DeclareLink",
    "LinkKind",
    "lookup_links",
    "module",
    "query_coverage",
    "query_paths",
    "query_reachability",
    "rebuild_topology",
    "RebuildTopology",
    "record_observed_link",
    "resolve_forwarding",
    "ResolveForwarding",
    "SCHEMA",
    "TopologyConflict",
    "TopologyError",
    "TopologyNotFound",
    "versions_dir",
    "withdraw_link",
]
