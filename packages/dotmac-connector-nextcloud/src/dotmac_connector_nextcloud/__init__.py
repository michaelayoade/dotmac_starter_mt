"""Nextcloud managed-collaboration connector public surface."""

from .declaration import CAPABILITY_IDS, CONNECTOR_KEY, MANIFEST
from .plugin import PLUGIN, NextcloudConnector
from .transport import (
    FailureKind,
    HttpxNextcloudTransport,
    ManagementRequest,
    NextcloudTransport,
    NextcloudTransportError,
    management_route,
    normalize_management_endpoint,
)

__version__ = "0.1.0a1"

__all__ = [
    "CAPABILITY_IDS",
    "CONNECTOR_KEY",
    "MANIFEST",
    "PLUGIN",
    "FailureKind",
    "HttpxNextcloudTransport",
    "ManagementRequest",
    "NextcloudConnector",
    "NextcloudTransport",
    "NextcloudTransportError",
    "__version__",
    "management_route",
    "normalize_management_endpoint",
]
