"""dotmac-connector-nira: the NiRA .ng registry connector.

A thin CoCCA-EPP transport plugin for the Integrator runtime. It speaks EPP
over TLS to the ``.ng`` registry and translates commands and queued messages
to and from typed events. Every domain decision — whether to register, what to
charge, when to retry — stays with the owning application and the engine.

Public surface: ``PLUGIN`` is the entry point the runtime discovers via the
``dotmac_integration.connectors`` group.
"""

from __future__ import annotations

from dotmac_connector_nira.delivery import (
    ACTIONS_BY_CAPABILITY,
    OUTBOUND_CAPABILITY_IDS,
    NiraDeliveryHandler,
)
from dotmac_connector_nira.plugin import (
    CONNECTOR_KEY,
    MANIFEST,
    PLUGIN,
    NiraConnector,
)
from dotmac_connector_nira.polling import MESSAGE_CAPABILITY, NiraPollHandler

__all__ = [
    "PLUGIN",
    "MANIFEST",
    "CONNECTOR_KEY",
    "NiraConnector",
    "NiraDeliveryHandler",
    "NiraPollHandler",
    "ACTIONS_BY_CAPABILITY",
    "OUTBOUND_CAPABILITY_IDS",
    "MESSAGE_CAPABILITY",
]
