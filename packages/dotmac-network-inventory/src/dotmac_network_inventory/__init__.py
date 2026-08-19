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
__all__ = [
    "__version__",
    "NetworkInventoryConflict",
    "NetworkInventoryError",
    "NetworkInventoryNotFound",
    "admit_node",
    "archive_node",
    "attach_vlan",
    "define_vlan",
    "lookup_interfaces",
    "lookup_nodes",
    "lookup_sites",
    "lookup_vlans",
    "module",
    "record_configuration_snapshot",
    "register_interface",
    "register_port",
    "register_site",
    "versions_dir",
]
