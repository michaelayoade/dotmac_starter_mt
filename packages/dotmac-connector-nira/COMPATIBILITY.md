# Compatibility

`dotmac-connector-nira 0.1.0a1` implements dotmac-integration SPI `>=1.4,<2.0`
under connector key `nira`, in DELIVERY and POLL modes. The floor is 1.4
because each capability declares its own mode: the eight `registry.*` command
capabilities are DELIVERY-only, and `registry.message.v1` is POLL-only. A
plugin-wide mode set (pre-1.4) could not express that a command binding must
never be handed the poll factory.

The POLL cursor is an opaque Integration-owned durable receipt marker. One call
returns at most one unacknowledged EPP queue head; a later call may acknowledge
that head only when the supplied cursor names it. This requires no connector
state and is compatible with the SPI 1.4 `(events, next_cursor)` shape. The
observation retains the serialized `resData` subtree, and configuration/login
refusals raise into Integration's durable poll-failure path.

## Public surface

`PLUGIN` (the entry point), `MANIFEST`, `CONNECTOR_KEY`, and the handler classes
`NiraDeliveryHandler` / `NiraPollHandler`. `ACTIONS_BY_CAPABILITY` and
`OUTBOUND_CAPABILITY_IDS` are exported so the one-operation-per-capability
allocation can be asserted by a conformance test. Everything under `epp.py` and
`frames.py` is transport internals and not part of the stable surface.

## Registry contract

A greeting-only probe against `ote.registry.ng` (svID NIRA) on 2026-08-26
confirmed `domain-1.0` / `host-1.0` / `contact-1.0` and `fee-1.0`. This version
requires those three objects, negotiates the fee extension it emits, and
allows no other egress hostname. The source IP is not yet whitelisted, so an
authenticated OT&E login is not proven and release remains mechanically held.
DNSSEC is not claimed or emitted by this version.

The DELIVERY contract is conservative until `dotmac-domains` publishes the
provider-neutral command and result schemas: successful read/check and transfer
query responses
require reconciliation rather than dropping their body, 2302/2303 require
identity reconciliation, and any transport/protocol failure after the business
command is attempted is outcome-unknown. `clTRID` is the correlation coordinate
for that repair; it is not treated as a provider idempotency guarantee.
