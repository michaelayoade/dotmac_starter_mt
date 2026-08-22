"""Public surface for the independently released Mono connector."""

from dotmac_connector_mono.plugin import (
    API_SECRET_KEY,
    CAPABILITY_ID,
    MANIFEST,
    PLUGIN,
    MonoError,
    MonoPlugin,
    MonoProtocolError,
    MonoRequestError,
)

__version__ = "0.1.0a1"

__all__ = [
    "API_SECRET_KEY",
    "CAPABILITY_ID",
    "MANIFEST",
    "PLUGIN",
    "MonoError",
    "MonoPlugin",
    "MonoProtocolError",
    "MonoRequestError",
    "__version__",
]
