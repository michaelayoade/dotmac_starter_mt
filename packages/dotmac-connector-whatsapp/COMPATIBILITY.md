# Compatibility

`dotmac-connector-whatsapp` 0.1.0a4 requires `dotmac-integration >=0.1.0a14`
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

The published 0.1.0a1, a2 and a3 manifests remain inside the distribution as
historical pins, each with its exact SPI range, configuration schema and
digest, so an installation on any of them resolves to a known contract rather
than an unknown digest. Moving a current installation to a2 replaces arbitrary
material-slot aliases with the manifest-owned binding names; that adoption
therefore requires a new config revision. The a2 handler continues to
understand the a1 configuration shape during the bounded adoption window.
Adoption to a3 is required before a `messaging.send.v1` binding can be created,
and adoption to a4 is required before a `messaging.templates.read.v1` binding
can be created or a template message can be sent under the pre-flight gate.

a3 is published — peeled tag `dotmac-connector-whatsapp-v0.1.0a3` points at
commit `70459efd468dd2dcc9e31693b9910b04fec21447`. That is why a4 exists at all:
the catalogue capability, the newly required `waba_id` and the attachment gates
change what `messaging.send.v1` accepts, and editing a published manifest in
place would leave one version number naming two different contracts.
