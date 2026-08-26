"""Realm-scoped Keycloak Admin connector for Dotmac Integrator."""

from .plugin import MANIFEST, PLUGIN, KeycloakAdminConnector
from .transport import (
    HttpxKeycloakTransport,
    KeycloakAdminRequest,
    KeycloakAdminResponse,
    KeycloakAdminTransport,
    KeycloakTransportError,
)

__version__ = "0.1.0a1"

__all__ = [
    "MANIFEST",
    "PLUGIN",
    "HttpxKeycloakTransport",
    "KeycloakAdminConnector",
    "KeycloakAdminRequest",
    "KeycloakAdminResponse",
    "KeycloakAdminTransport",
    "KeycloakTransportError",
    "__version__",
]
