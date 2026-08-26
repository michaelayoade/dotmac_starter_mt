"""Bounded Contabo connector for Dotmac Integrator."""

from .plugin import (
    ACTIVATION_GATES,
    MANIFEST,
    PLUGIN,
    CapabilityActivationGate,
    ContaboActivationError,
    ContaboConnector,
    activation_gate_for,
)
from .transport import (
    ContaboRequest,
    ContaboResponse,
    ContaboTransport,
    ContaboTransportError,
    FailureKind,
    HttpxContaboTransport,
    normalize_api_endpoint,
)

__version__ = "0.1.0a1"

__all__ = [
    "ACTIVATION_GATES",
    "MANIFEST",
    "PLUGIN",
    "CapabilityActivationGate",
    "ContaboActivationError",
    "ContaboConnector",
    "ContaboRequest",
    "ContaboResponse",
    "ContaboTransport",
    "ContaboTransportError",
    "FailureKind",
    "HttpxContaboTransport",
    "__version__",
    "activation_gate_for",
    "normalize_api_endpoint",
]
