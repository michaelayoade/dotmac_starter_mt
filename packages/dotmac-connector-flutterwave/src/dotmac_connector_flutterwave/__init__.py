"""Public surface for the Dotmac Flutterwave connector."""

from dotmac_connector_flutterwave.plugin import (
    MANIFEST,
    PLUGIN,
    FlutterwaveConnector,
)

__version__ = "0.1.0a2"

__all__ = ["MANIFEST", "PLUGIN", "FlutterwaveConnector", "__version__"]
