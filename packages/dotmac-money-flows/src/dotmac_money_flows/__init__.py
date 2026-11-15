"""Public surface of ``dotmac-money-flows`` (module plumbing only).

The flow commands and typed contracts arrive with the first flow. This release
skeleton declares the manifest, the instance-table allowlist, the migration
lineage and the event types, and nothing else.
"""

from dotmac_money_flows.events import (
    SETTLE_ATTENTION_EVENT_TYPE,
    SETTLE_SETTLED_EVENT_TYPE,
)
from dotmac_money_flows.manifest import module
from dotmac_money_flows.migrations import versions_dir

__version__ = "0.1.0a1"

__all__ = [
    "SETTLE_ATTENTION_EVENT_TYPE",
    "SETTLE_SETTLED_EVENT_TYPE",
    "__version__",
    "module",
    "versions_dir",
]
