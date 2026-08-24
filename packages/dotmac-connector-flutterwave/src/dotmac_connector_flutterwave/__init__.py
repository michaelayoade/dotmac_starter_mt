"""Public surface for the Dotmac Flutterwave connector."""

from dotmac_connector_flutterwave.outbound import (
    INTENT_CAPABILITY_ID,
    REFUND_CAPABILITY_ID,
    CommandContractError,
    FlutterwaveDeliveryHandler,
)
from dotmac_connector_flutterwave.plugin import (
    MANIFEST,
    PLUGIN,
    FlutterwaveConnector,
)

__version__ = "0.1.0a2"

__all__ = [
    "INTENT_CAPABILITY_ID",
    "MANIFEST",
    "PLUGIN",
    "REFUND_CAPABILITY_ID",
    "CommandContractError",
    "FlutterwaveConnector",
    "FlutterwaveDeliveryHandler",
    "__version__",
]
