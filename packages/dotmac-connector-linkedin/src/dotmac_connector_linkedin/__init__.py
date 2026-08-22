"""Public surface for the LinkedIn connector plugin."""

from dotmac_connector_linkedin.plugin import (
    MANIFEST,
    PLUGIN,
    LinkedInPayloadInvalid,
    LinkedInPlugin,
)

__version__ = "0.1.0a1"

__all__ = [
    "MANIFEST",
    "PLUGIN",
    "LinkedInPayloadInvalid",
    "LinkedInPlugin",
    "__version__",
]
