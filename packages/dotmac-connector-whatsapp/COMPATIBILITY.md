# Compatibility

`dotmac-connector-whatsapp` 0.1.0a3 requires `dotmac-integration >=0.1.0a14`
and SPI `>=1.4,<2.0`. It declares `ConnectorMode.INGRESS`,
`ConnectorMode.POLL` and `ConnectorMode.DELIVERY`, capabilities
`messaging.receive.v1`, `messaging.send.v1` and `messaging.templates.read.v1`,
four exact logical secret bindings, and exact egress to `graph.facebook.com`.

`messaging.templates.read.v1` is Sub's existing production capability id. This
distribution claims to IMPLEMENT it; the business domain owner declares it, and
a deployment that has not declared it cannot bind this capability.

`messaging.send.v1` now requires `waba_id` in its configuration: the
approved-template pre-flight has no fail-open branch, so a binding that cannot
name its account cannot be activated. An installation adopting this manifest
therefore needs a new config revision even if nothing else about it changed.

The published 0.1.0a1 and a2 manifests remain inside the distribution as historical
pin with its exact SPI `>=1.2,<2.0`, configuration schema and digest. Moving a
current installation to a2 replaces arbitrary material-slot aliases with the
manifest-owned binding names; that adoption therefore requires a new config
revision. The a2 handler continues to understand the a1 configuration shape
during the bounded adoption window. Adoption to a3 is required before a
`messaging.send.v1` binding can be created.
