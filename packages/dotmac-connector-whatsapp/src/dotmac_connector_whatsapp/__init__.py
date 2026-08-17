"""Meta WhatsApp Cloud API ingress connector.

The distribution translates and authenticates provider wire bytes. It owns no
database, retry loop, checkpoint, destination decision, or product state.
"""

from dotmac_connector_whatsapp.plugin import MANIFEST, PLUGIN, WhatsAppConnector

__version__ = "0.1.0a1"

__all__ = ["MANIFEST", "PLUGIN", "WhatsAppConnector", "__version__"]
