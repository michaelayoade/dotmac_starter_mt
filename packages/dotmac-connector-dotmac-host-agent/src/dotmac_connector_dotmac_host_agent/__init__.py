"""Constrained mTLS Dotmac host-agent connector."""

from .plugin import (
    ACTIVATION_GATES,
    MANIFEST,
    PLUGIN,
    CapabilityActivationGate,
    DotmacHostAgentConnector,
    HostAgentProtocolError,
    activation_gate_for,
)
from .transport import (
    FailureKind,
    HostAgentRequest,
    HostAgentResponse,
    HostAgentTransport,
    HostAgentTransportError,
    HttpxHostAgentTransport,
    normalize_agent_endpoint,
)

__version__ = "0.1.0a1"

__all__ = [
    "ACTIVATION_GATES",
    "MANIFEST",
    "PLUGIN",
    "CapabilityActivationGate",
    "DotmacHostAgentConnector",
    "FailureKind",
    "HostAgentProtocolError",
    "HostAgentRequest",
    "HostAgentResponse",
    "HostAgentTransport",
    "HostAgentTransportError",
    "HttpxHostAgentTransport",
    "__version__",
    "activation_gate_for",
    "normalize_agent_endpoint",
]
