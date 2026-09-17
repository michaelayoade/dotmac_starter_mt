# Changelog

## 0.1.0a1 — unreleased

Not published; no release run, no peeled tag. Not pinned into
`dotmac_integrator`'s dependencies.

- Adds a POLL-only observation capability,
  `invoices.accounting_sync.observation.v1`, over Sub's
  `GET /api/v1/invoices/accounting-sync/v2` feed (`X-API-Key` authenticated,
  egress `selfcare.dotmac.io`).
- Cursor is canonical compact JSON `{"after_id","after_updated_at"}`; a
  partial cursor is refused as a connector-internal error. Validates
  strictly-increasing `(updated_at, source_invoice_id)` page positions and
  refuses a non-monotonic page rather than accepting it silently. An empty
  page (including the first poll) returns no events and leaves the cursor
  unchanged.
- Emits only source-derived fields — never `organization_id` or an
  `idempotency_key`-shaped field. Those are supplied downstream by the
  generic Integration engine and the destination `ObservationPortClient`;
  Sub's `account_id` is never mapped onto ERP's `organization_id`.
- Computes `projection_fingerprint` as a best-effort match of ERP's shadow
  mapper's canonicalization algorithm. **Not verified against ERP's real
  implementation** — the ERP worktree was unreachable while building this
  connector. See `mapping.py`'s docstring and `COMPATIBILITY.md`'s "Known
  limitation" section.
- Validates the disposition/issues consistency rule (a blocked observation
  with no issues, or a non-blocked observation carrying issues, is refused)
  before emitting, mirroring ERP's own `RecordInvoiceSyncOutcome`
  construction rule as defense in depth.
- Declares `claims_contract_digest=None` on its one capability — the
  parallel ERP-side slice (1b) has not yet published the capability contract
  this connector's payload should match. Left explicit rather than faked;
  see the `TODO` in `plugin.py`.
