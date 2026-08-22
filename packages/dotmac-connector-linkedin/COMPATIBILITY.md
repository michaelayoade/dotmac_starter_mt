# Compatibility — dotmac-connector-linkedin

| Surface | Contract |
|---|---|
| Version | `0.1.0a1` (declared, unreleased) |
| Integration floor | `dotmac-integration >=0.1.0a11` |
| SPI | `>=1.3,<2.0` |
| Mode | `INGRESS` only |
| Capabilities | `social.activity.observation.v1`, `marketing.lead.observation.v1` |
| Egress | deny all |

`X-LI-Signature` is exactly 64 lowercase hexadecimal characters. The literal
`hmacsha256=` prefix is prepended to the raw body before HMAC calculation and is
not part of the header value. Organization-social bodies traverse every member
of `notifications`; Lead Sync identity is the documented response-URN plus
`occurredAt` pair.
