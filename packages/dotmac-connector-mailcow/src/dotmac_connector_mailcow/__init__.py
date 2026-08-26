"""Public surface for the stateless Mailcow connector."""

from .declaration import MANIFEST, VERSION
from .plugin import PLUGIN, MailcowConnector, MailcowProvisioningHandler
from .transport import (
    HttpxMailcowTransport,
    MailcowRequest,
    MailcowResponse,
    MailcowTransport,
    MailcowTransportError,
)

__version__ = VERSION

__all__ = [
    "MANIFEST",
    "PLUGIN",
    "HttpxMailcowTransport",
    "MailcowConnector",
    "MailcowProvisioningHandler",
    "MailcowRequest",
    "MailcowResponse",
    "MailcowTransport",
    "MailcowTransportError",
    "__version__",
]
