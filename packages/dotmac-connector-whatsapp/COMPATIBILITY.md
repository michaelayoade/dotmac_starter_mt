# Compatibility

| Surface | Contract |
|---|---|
| Connector key | `meta_whatsapp` |
| Capability | `messaging.receive.v1` |
| Modes | `INGRESS` only |
| SPI | `>=1.1,<2.0` |
| Integration floor | `0.1.0a6` |

`0.1.0a1` is the first package version and is not yet published. Adding a
capability, an outbound mode, or a second provider is an explicit contract and
ADR authorization change, not an implementation detail.
