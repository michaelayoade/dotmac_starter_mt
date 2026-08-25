# Compatibility — dotmac-connector-remita

| Surface | Contract |
|---|---|
| Distribution | `dotmac-connector-remita` |
| Connector key | `remita` |
| Version | `0.1.0a1` released; the working tree adds issuance and needs a new version before publishing |
| Integration floor | `dotmac-integration >=0.1.0a14` |
| SPI | `>=1.4,<2.0` |
| Modes | `POLL` (status) and `DELIVERY` (issuance) |
| Capabilities | `payments.reference.status.observation.v1` (POLL), `payments.reference.issuance.v1` (DELIVERY) |
| Egress | `demo.remita.net`, `login.remita.net` |
| Provider idempotency | none — Remita accepts no idempotency header; the product-minted `orderId` is the only natural key |

Provider codes and messages are evidence, not product lifecycle values.

RRR issuance became available once the engine gained a durable
command-response seam: `Outcome.provider_reference` and `provider_status_code`
are persisted on `delivery_attempts` by `ig_0012_delivery_evidence`, so the
minted reference survives the call instead of disappearing into a
fire-and-forget dispatch.

The published `0.1.0a1` poll-only manifest is retained as a historical manifest
so an installation pinned to that digest still resolves.
