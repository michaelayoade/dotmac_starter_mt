"""Public surface for the SMTP delivery connector (Dotmac Integrator, DELIVERY)."""

from dotmac_connector_smtp.classify import classify_reply, enhanced_status
from dotmac_connector_smtp.config import SmtpConfig, SmtpConfigError
from dotmac_connector_smtp.connection import (
    CONFIG_SCHEMA,
    SECRET_PASSWORD,
    Endpoint,
    endpoint_config,
)
from dotmac_connector_smtp.plugin import (
    CONNECTOR_KEY,
    MANIFEST,
    PLUGIN,
    SEND_CAPABILITY,
    SmtpConnector,
    SmtpDeliveryHandler,
)
from dotmac_connector_smtp.provider import (
    SendResult,
    SmtpOutcomeUnknown,
    SmtpTransport,
)

__version__ = "0.1.0a1"

__all__ = [
    "CONFIG_SCHEMA",
    "CONNECTOR_KEY",
    "MANIFEST",
    "PLUGIN",
    "SECRET_PASSWORD",
    "SEND_CAPABILITY",
    "Endpoint",
    "SendResult",
    "SmtpConfig",
    "SmtpConfigError",
    "SmtpConnector",
    "SmtpDeliveryHandler",
    "SmtpOutcomeUnknown",
    "SmtpTransport",
    "__version__",
    "classify_reply",
    "endpoint_config",
    "enhanced_status",
]
