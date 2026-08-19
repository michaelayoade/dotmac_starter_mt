"""Public contract for reusable managed Network Inventory."""

from dotmac_network_inventory.manifest import module
from dotmac_network_inventory.migrations import versions_dir
from dotmac_network_inventory.service import (
    NetworkInventoryConflict,
    NetworkInventoryError,
    NetworkInventoryNotFound,
    admit_node,
    archive_node,
    attach_vlan,
    define_vlan,
    lookup_interfaces,
    lookup_nodes,
    lookup_sites,
    lookup_vlans,
    record_configuration_snapshot,
    register_interface,
    register_port,
    register_site,
)

__version__ = "0.1.0a1"
from dotmac_network_inventory.contracts import (
    AdmitNode,
    ArchiveNode,
    NodeKind,
    NodeState,
    RegisterInterface,
    RegisterSite,
)
from dotmac_network_inventory.models import (
    ALL_MODELS,
    SCHEMA,
)

__all__ = [
    "__version__",
    "admit_node",
    "AdmitNode",
    "ALL_MODELS",
    "archive_node",
    "ArchiveNode",
    "attach_vlan",
    "define_vlan",
    "lookup_interfaces",
    "lookup_nodes",
    "lookup_sites",
    "lookup_vlans",
    "module",
    "NetworkInventoryConflict",
    "NetworkInventoryError",
    "NetworkInventoryNotFound",
    "NodeKind",
    "NodeState",
    "record_configuration_snapshot",
    "register_interface",
    "register_port",
    "register_site",
    "RegisterInterface",
    "RegisterSite",
    "SCHEMA",
    "versions_dir",
]
