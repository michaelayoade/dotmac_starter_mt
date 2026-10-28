# Changelog

## 0.1.0a1 — unreleased

Not published; no release run, no peeled tag. Held back in
`.github/release-connectors.json`; not pinned into `dotmac_integrator`'s
dependencies.

- Adds PROVISION `network.access_projection.v1`: incumbent-exact rendering of
  `radcheck`/`radreply`/`radusergroup`, advisory-locked delete-and-insert,
  read-back verification with the password compared by presence only, one
  single-use secret fetch per enabled apply, rename handled as old-login
  deletion in the same transaction.
- Adds POLL `network.access_observation.v1`: dual-stream
  `radpostauth`/`radacct` cursor with stable row fingerprints; junk rows are
  skipped but never stall the cursor.
- Adds PROVISION `network.session_disconnect.v1`: stdlib RFC 5176
  Disconnect with response-authenticator verification, gated on SPI 1.7
  bound network targets before any I/O.
- Claims Sub's three published contract digests exactly.
