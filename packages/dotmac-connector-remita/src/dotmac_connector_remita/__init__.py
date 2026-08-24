"""Public surface for the Remita connector plugin."""

from dotmac_connector_remita.outbound import (
    ISSUANCE_CAPABILITY_ID,
    CommandContractError,
    RemitaIssuanceHandler,
)
from dotmac_connector_remita.plugin import (
    MANIFEST,
    PLUGIN,
    RemitaError,
    RemitaPlugin,
    RemitaProtocolError,
    RemitaRequestError,
)

__version__ = "0.1.0a1"

__all__ = [
    "ISSUANCE_CAPABILITY_ID",
    "MANIFEST",
    "PLUGIN",
    "CommandContractError",
    "RemitaError",
    "RemitaIssuanceHandler",
    "RemitaPlugin",
    "RemitaProtocolError",
    "RemitaRequestError",
    "__version__",
]
