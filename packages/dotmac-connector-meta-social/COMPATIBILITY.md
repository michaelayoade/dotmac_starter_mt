# Compatibility

`dotmac-connector-meta-social` implements dotmac-integration SPI `>=1.4,<2.0`
and connector key `meta_social`, with two capabilities mapped to two modes:

| capability | mode |
| --- | --- |
| `messaging.receive.v1` | INGRESS |
| `messaging.send.v1` | DELIVERY |

The SPI floor is 1.4 rather than 1.3 because `CapabilityDeclaration.modes` —
the per-capability mode mapping that keeps an engine from asking for an ingress
handler for the send capability — first exists there.

It declares `webhook_signing_secret`, optional
`webhook_signing_previous_secret`, `webhook_verify_token`, and three optional
delivery bindings: `facebook_page_access_token`,
`instagram_login_access_token` (individual auth mode) and
`meta_oauth_access_token` (shared OAuth auth mode). Its external host set is
exactly `graph.facebook.com` and `graph.instagram.com`.

A `messaging.send.v1` binding requires `graph_api_version`, `auth_mode` and
`timeout_seconds` in configuration, plus the `facebook_page_id` and
`instagram_account_id` the installation speaks for. There is deliberately no
default Graph API version: an API version is a compatibility decision, not a
value that may age silently inside a released wheel.

`messaging.send.v1` carries a decision the product already took. It does not
evaluate messaging windows or response eligibility, and the connector will
never gain a configuration knob that asks it to.
