"""Public surface for the Dotmac Paystack connector."""

from dotmac_connector_paystack.delivery import (
    ACTIONS_BY_CAPABILITY,
    OUTBOUND_CAPABILITY_IDS,
    PaystackDeliveryHandler,
)
from dotmac_connector_paystack.operations import (
    OPERATIONS,
    OperationOutcome,
    OperationResult,
    PaystackOperations,
)
from dotmac_connector_paystack.plugin import MANIFEST, PLUGIN, PaystackConnector

__version__ = "0.1.0a2"

__all__ = [
    "ACTIONS_BY_CAPABILITY",
    "MANIFEST",
    "OPERATIONS",
    "OUTBOUND_CAPABILITY_IDS",
    "PLUGIN",
    "OperationOutcome",
    "OperationResult",
    "PaystackConnector",
    "PaystackDeliveryHandler",
    "PaystackOperations",
    "__version__",
]
