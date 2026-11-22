# Changelog

## 0.1.0a1 (unreleased)

Not published; no release run, no peeled tag. Held back in
`.github/release-connectors.json` (`release_enabled: false`).

- Initial SMTP delivery connector (work package B4 of the Academy companion
  plan). The transport, MIME builder, classification table and configuration
  validator MOVED from the never-released `dotmac-delivery-smtp`
  (`provider.py`, `classify.py`, `config.py`); they were not rewritten.
  `classify.py` differs only in its import block, `config.py` not at all, both
  pinned by `tests/test_classification.py`.
- DELIVERY `email.send.v1`; one Outcome per dispatch.
- NEW before any I/O: the destination must lie inside a network target the
  operator bound to the installation (`require_network_target`), and the
  connection goes to the pinned, already-checked address (no name resolved at
  send time). The TLS server name is the configured host, so the certificate
  hostname is verified.
- NEW: every failure after the DATA payload was sent (lost reply or any
  non-250 final reply) is `RECONCILIATION_REQUIRED`, never retried.
- MIME header order is fixed (the sender's canonical order) so a JSON round
  trip through the outbox cannot change the bytes.
- Dropped by design: the kernel `SecretSource`, the product admission hook and
  mail-gate lease, the kernel settings specs and reader (the Integrator owns
  configuration revisions, secret materialisation and leasing).
- Known dependency: the engine must deliver bound network targets to DELIVERY
  handlers. `dotmac_integration.dispatch.prepare` does not today (only the
  PROVISION path writes `_network_targets` into the handler config), so until
  it does the connector fails closed: every send is refused with
  `network_target_refused`.
