# dotmac-connector-smtp

SMTP submission connector for the independently deployed Dotmac Integrator
(for the Academy companion). Over SPI `>=1.7,<2.0` it serves one capability:

| capability | mode | what it does |
|---|---|---|
| `email.send.v1` | DELIVERY | submits one rendered message over SMTP and returns one classified `Outcome` |

- **Destination control.** The SMTP destination is an operator-bound network
  target. Before any I/O the connector calls `require_network_target` for the
  configured `address:port`, and connects to exactly that pinned address; no
  name is resolved afterwards. Anything outside the bound targets is refused.
- **STARTTLS always.** TLS 1.2 minimum, certificate chain and hostname
  verified against `host`; no plaintext mode, no credentials before TLS.
- **Ambiguity after DATA.** A lost reply, or any non-250 final reply, after the
  payload was sent is `RECONCILIATION_REQUIRED` and is never retried: SMTP has
  no idempotency key.
- **Moved, not rewritten.** `provider.py`, `classify.py` and `config.py` came
  from the never-released `dotmac-delivery-smtp`; the classification table is
  byte-identical (pinned by `tests/test_classification.py`).
- **Settings and secrets by name.** Configuration (`host`, `address`, `port`,
  `username`, `from_address`) lives in the installation's configuration
  revision; the only secret is the `smtp_password` binding. Nothing here
  carries a default credential, host or address.

## Destination model: `host` plus a pinned `address`

Michael approved this model (2026-11-22). `host` is the TLS server name the
certificate is verified against; `address` is the IP literal the connection is
actually made to, so no name is ever resolved at send time. The **operator**
binds the installation's network target as a **/32** (or /128) for exactly that
address, tcp 587 (the schema pins `port` to 587 and the manifest ceiling is tcp
587 only). The manage scope cannot bind targets and cannot change `address`:
the Integrator manage caller already refuses destination keys (`host`,
`address`, `port`, `username`), and any change to them must come with a fresh
secret, so held credentials are never presented to a new destination.

## Timing and the lease

`SEND_DEADLINE_SECONDS` (240) is a hard wall-clock cap on one whole send: a
watchdog shuts the socket at the deadline, so a server trickling bytes cannot
outlast it (before DATA: retryable; after DATA: ambiguous). **The Integrator
pump must assert at boot that its attempt lease is greater than
`SEND_DEADLINE_SECONDS`** (not the 285 s nominal per-operation sum, and not its
own data budget).

Owns no database, queue, retry, consent, rendering or attachment storage.
Tests are hermetic: a scripted SMTP sink on loopback with throwaway
certificates; no real network. Run them with
`poetry run pytest packages/dotmac-connector-smtp/tests`.

Release is held back (`.github/release-connectors.json`). See
`COMPATIBILITY.md` for floors, the outcome table, the timeout budget and the
open engine dependency.
