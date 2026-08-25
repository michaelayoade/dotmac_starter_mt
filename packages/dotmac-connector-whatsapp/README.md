# dotmac-connector-whatsapp

Ingress-only Meta WhatsApp adapter for the Dotmac Integrator. It verifies
Meta's raw-body HMAC, answers the subscription handshake, and translates
WhatsApp message/status webhooks into `messaging.receive.v1` observations.

It owns no rows, retry loop, checkpoint, provider client or product decision.
`dotmac-integration` owns receipt identity and persistence; Sub owns what an
inbound message or delivery status means.

Configuration names held-material slots rather than values:

```json
{
  "signing_slots": ["whatsapp_signing_current", "whatsapp_signing_previous"],
  "challenge_slot": "whatsapp_verify_token"
}
```

The rotation window evaluates every signing slot without short-circuiting.
Remove the previous slot after Meta and the Integrator have converged on the
new signing material. GET handshake eligibility is independent of POST delivery
eligibility; the module deliberately allows a configured-but-disabled binding
to complete activation while refusing POST until enabled.

The package targets SPI `>=1.1,<2.0` and depends on
`dotmac-integration>=0.1.0a6`. It is not publishable until that module release
exists; a source version is not an installable floor.
