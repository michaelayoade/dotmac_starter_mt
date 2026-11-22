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

Owns no database, queue, retry, consent, rendering or attachment storage.
Tests are hermetic: a scripted SMTP sink on loopback with throwaway
certificates; no real network. Run them with
`poetry run pytest packages/dotmac-connector-smtp/tests`.

Release is held back (`.github/release-connectors.json`). See
`COMPATIBILITY.md` for floors, the outcome table, the timeout budget and the
open engine dependency.
