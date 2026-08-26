# Compatibility

`dotmac-connector-nira 0.1.0a1` implements dotmac-integration SPI `>=1.4,<2.0`
under connector key `nira`, in DELIVERY and POLL modes. The floor is 1.4
because each capability declares its own mode: the eight `registry.*` command
capabilities are DELIVERY-only, and `registry.message.v1` is POLL-only. A
plugin-wide mode set (pre-1.4) could not express that a command binding must
never be handed the poll factory.

## Public surface

`PLUGIN` (the entry point), `MANIFEST`, `CONNECTOR_KEY`, and the handler classes
`NiraDeliveryHandler` / `NiraPollHandler`. `ACTIONS_BY_CAPABILITY` and
`OUTBOUND_CAPABILITY_IDS` are exported so the one-operation-per-capability
allocation can be asserted by a conformance test. Everything under `epp.py` and
`frames.py` is transport internals and not part of the stable surface.

## Registry contract

Confirmed live against `ote.registry.ng` (svID NIRA) 2026-08-26: objects
`domain-1.0` / `host-1.0` / `contact-1.0`, extensions `rgp-1.0` / `secDNS-1.1` /
`fee-1.0` and the CoCCA finance extension. The connector requires the three
objects and speaks the fee extension on availability. DNSSEC (secDNS) frame
support is declared-compatible but not yet emitted — a domain-update DNSSEC
slice is the next capability.
