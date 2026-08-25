"""Meta WhatsApp ingress connector — provider wire translation, and only that."""

from dotmac_connector_whatsapp.connector import MANIFEST, PLUGIN, WhatsAppPlugin

__version__ = "0.1.0a1"

__all__ = ["MANIFEST", "PLUGIN", "WhatsAppPlugin", "__version__"]
