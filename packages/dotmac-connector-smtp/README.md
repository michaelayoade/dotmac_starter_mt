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
actually made to, so no name is ever resolved at send time. The intent is that
the **operator** binds the installation's network target as a **/32** (or /128)
for exactly that address, tcp 587 (the schema pins `port` to 587 and the manifest
ceiling is tcp 587 only).

**What is enforced today, honestly:** the connector checks the address against
the bound targets before any I/O, and the manifest ceiling is tcp 587. It does
**not** enforce a /32: any bound CIDR covering the address and port is accepted,
so the /32 is an operator convention until a gate checks it. The `address` key is
refused under the manage scope by Integrator main 03c18b4f (#35), so the manage
caller cannot repoint the destination; that guard lives in the Integrator, not in
this package.

## Timing and the lease

`SEND_DEADLINE_SECONDS` (240) is a hard wall-clock cap on one whole send: a
watchdog shuts the raw socket down at the deadline (never the TLS object's own
`shutdown`, so nothing is written in plaintext), and the session checks the
deadline immediately before AUTH and before DATA. A server trickling bytes
cannot outlast it (before DATA: retryable; after DATA: ambiguous). **Real worst
case: about 255 s**, because a TLS handshake already in progress is not
interruptible (240 s plus up to one 15 s operation). **The Integrator pump must
check its attempt lease against the connector's deadline** (an Integrator
follow-up: assert at boot that the lease is greater than `SEND_DEADLINE_SECONDS`,
with real margin over the 255 s worst case), not against the 285 s nominal
per-operation sum or its own data budget. The connector's `dotmac-integration`
floor rises to **a22** (the release carrying the DELIVERY `_network_targets`
fix) when a22 is tagged; the package and release policy stay at `>=a20` until
then.

Owns no database, queue, retry, consent, rendering or attachment storage.
Tests are hermetic: a scripted SMTP sink on loopback with throwaway
certificates; no real network. Run them with
`poetry run pytest packages/dotmac-connector-smtp/tests`.

Release is held back (`.github/release-connectors.json`). See
`COMPATIBILITY.md` for floors, the outcome table, the timeout budget and the
open engine dependency.
