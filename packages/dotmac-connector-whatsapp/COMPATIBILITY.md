# Compatibility

`dotmac-connector-whatsapp` 0.1.0a2 requires `dotmac-integration >=0.1.0a10`
and SPI `>=1.3,<2.0`. It declares only `ConnectorMode.INGRESS`, capability
`messaging.receive.v1`, three exact logical secret bindings, and explicit
deny-all provider egress.

The published 0.1.0a1 manifest remains inside the distribution as a historical
pin with its exact SPI `>=1.2,<2.0`, configuration schema and digest. Moving a
current installation to a2 replaces arbitrary material-slot aliases with the
manifest-owned binding names; that adoption therefore requires a new config
revision. The a2 handler continues to understand the a1 configuration shape
during the bounded adoption window.
