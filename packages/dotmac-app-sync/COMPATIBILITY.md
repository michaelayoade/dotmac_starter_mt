# Compatibility

| Surface | Contract |
|---|---|
| Python | `>=3.11,<3.14` |
| Envelope | Capability identifiers end in an explicit `.vN`; the wire also carries that numeric version and mismatches fail closed |
| Authentication | Supplied by the destination adapter as `AuthenticatedPeer`; this package never authenticates a network request |
| Delivery | `SyncReceiver.receive` owns atomic deduplication and local resolver invocation; this package passes a stable idempotency key and payload-sensitive fingerprint |
| Persistence | None |
| Transport | None |

Minor wire evolution uses a new capability version. A source event identity
reused with changed payload retains its idempotency key and changes its
fingerprint so the destination can refuse the conflict rather than replay it.
