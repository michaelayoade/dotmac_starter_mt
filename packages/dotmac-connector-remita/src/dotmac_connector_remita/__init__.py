"""Public surface for the Remita connector plugin."""

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
    "MANIFEST",
    "PLUGIN",
    "RemitaError",
    "RemitaPlugin",
    "RemitaProtocolError",
    "RemitaRequestError",
    "__version__",
]
