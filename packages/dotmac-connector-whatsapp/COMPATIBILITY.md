# Compatibility

`dotmac-connector-whatsapp` 0.1.0a3 requires `dotmac-integration >=0.1.0a14`
and SPI `>=1.4,<2.0`. It declares `ConnectorMode.INGRESS` and
`ConnectorMode.DELIVERY`, capabilities `messaging.receive.v1` and
`messaging.send.v1`, four exact logical secret bindings, and exact egress to
`graph.facebook.com`.

The published 0.1.0a1 and a2 manifests remain inside the distribution as historical
pin with its exact SPI `>=1.2,<2.0`, configuration schema and digest. Moving a
current installation to a2 replaces arbitrary material-slot aliases with the
manifest-owned binding names; that adoption therefore requires a new config
revision. The a2 handler continues to understand the a1 configuration shape
during the bounded adoption window. Adoption to a3 is required before a
`messaging.send.v1` binding can be created.
