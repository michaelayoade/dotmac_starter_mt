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

Per-installation config (OT&E vs production differ only by these values):
`host`, `port` (700), `clid`, optional `currency`, and required
`connect_timeout` / `read_timeout`.

Secrets, materialized per call and never persisted:

- `epp_password` — the registrar EPP password.
- `client_pem` (optional) — client certificate and private key concatenated
  into one PEM, when the registry requires mutual TLS.

Two things the connector cannot fix and must not mask:

1. The source IP must be **whitelisted registry-side**. An unwhitelisted IP
   fails login with `2202`, which the connector reports as terminal.
2. A bad or absent client cert fails the TLS handshake. Supply the combined PEM.

## Layout

- `epp.py` — TLS socket, RFC 5734 framing, result-code classification.
- `frames.py` — the command XML builders (pure wire translation).
- `delivery.py` — the DELIVERY SPI adapter and per-capability operation
  allow-list.
- `polling.py` — the POLL SPI adapter over the registry message queue.
- `plugin.py` — the versioned manifest and connection health check.

The wire shape follows the CoCCA reference module NiRA distributes to WHMCS
registrars; this is an independent Python implementation of that documented
contract.
