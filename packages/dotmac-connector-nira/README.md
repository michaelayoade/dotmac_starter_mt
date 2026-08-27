# dotmac-connector-nira

The NiRA `.ng` registry connector: a thin CoCCA-EPP transport plugin for the
Dotmac Integrator runtime.

It speaks EPP over TLS (RFC 5730/5734) to the `.ng` registry and translates
commands and queued messages to and from typed events. It decides nothing about
domains — whether to register a name, what to charge, or when to retry all
belong to the owning application (`dotmac-domains`) and the engine
(`dotmac-integration`). The registry is a transport, not a decision system.

## What it provides

Eight **DELIVERY** capabilities (outbound EPP commands) and one **POLL**
capability (the registry message queue):

| Capability | Mode | EPP |
|---|---|---|
| `registry.availability.v1` | delivery | `domain:check` + fee ext |
| `registry.domain_info.v1` | delivery | `domain:info` |
| `registry.domain_register.v1` | delivery | `domain:create` |
| `registry.domain_renew.v1` | delivery | `domain:renew` |
| `registry.domain_update.v1` | delivery | `domain:update` (NS) |
| `registry.domain_transfer.v1` | delivery | `domain:transfer` |
| `registry.host_provision.v1` | delivery | `host:create` / `host:check` — glue |
| `registry.contact_provision.v1` | delivery | `contact:check` |
| `registry.message.v1` | poll | `poll req` / `poll ack` |

There is deliberately **no INGRESS** capability: EPP has no webhook, so there is
no HTTP body to authenticate. Inbound is poll-only.

## Configuration

This held-back version permits only the reviewed OT&E host
`ote.registry.ng`. Config supplies that exact `host`, `port` (700), `clid`,
optional `currency`, and required `connect_timeout` / `read_timeout`.
Production cannot be enabled by changing config: its exact hostname must first
be reviewed into a later manifest release.

Secrets, materialized per call and never persisted:

- `epp_password` — the registrar EPP password.
- `client_pem` (optional) — client certificate and private key concatenated
  into one PEM, when the registry requires mutual TLS.

Two things the connector cannot fix and must not mask:

1. The source IP must be **whitelisted registry-side**. An unwhitelisted IP
   fails login with `2202`, which the connector reports as terminal.
2. A bad or absent client cert fails the TLS handshake. Supply the combined PEM.

`validate_connection` performs the real login and logout; a greeting-only
connection is not health. The release inventory keeps this distribution at
`release_enabled: false` until OT&E login succeeds from Integrator and the
owning domains application publishes the command/result contracts. Until that
result owner exists, successful availability/info/check responses are parked
for reconciliation instead of being reported as body-less successes.

The engine idempotency key travels as EPP `clTRID` for correlation. NiRA has not
proved `clTRID` to be a deduplication guarantee, so a disconnect after sending a
business command is `reconciliation_required`; it is never automatically
retried. Likewise, EPP 2302/2303 do not prove that an existing/missing object
matches local identity and are reconciled rather than treated as success.

## Layout

- `epp.py` — TLS socket, RFC 5734 framing, result-code classification.
- `frames.py` — the command XML builders (pure wire translation).
- `delivery.py` — the DELIVERY SPI adapter and per-capability operation
  allow-list.
- `polling.py` — the POLL SPI adapter over the registry message queue.
- `plugin.py` — the versioned manifest and connection health check.

Polling uses a two-phase queue handshake. A call returns at most one queue head
without acknowledging it, preserving both the human message and serialized
`resData` subtree before the provider copy can be deleted. Only a later call
whose persisted Integration cursor matches that same live head sends `poll
ack`; it then returns the next head, again unacknowledged. A crash before
Integration commits therefore redelivers the head, and an ambiguous
acknowledgement is resolved by reading the live head on retry. Invalid runtime
configuration and rejected login raise so Integration records failure/backoff;
they are never reported as an empty successful poll. The connector owns no
checkpoint or retry ledger.

The wire shape follows the CoCCA reference module NiRA distributes to WHMCS
registrars; this is an independent Python implementation of that documented
contract.
