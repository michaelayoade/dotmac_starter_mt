# Changelog — dotmac-commercial-agreements

All notable changes to the `dotmac-commercial-agreements` distribution. This
package follows [Semantic Versioning](https://semver.org). Pre-1.0 (`0.x`, incl.
this alpha) the surface is still settling — a `0.MINOR` bump may carry breaking
changes, each called out here.

## 0.1.0a1 — 2026-08-19

First release. Product-first extraction of the vendor control plane's
`contracts/` service (ADR-0057 § 1).

### Added

- `mod_agreements` — `agreements`, `agreement_lines`, `agreement_events` on the
  platform plane. Lineage root `cg_0001_agreements`, which verifies
  `idempotency_ledger.v1` and `platform_audit_log.v1` before any DDL of its own.
- The full lifecycle as named, guarded, idempotent commands: `open_draft`,
  `propose`, `approve`, `reject`, `activate`, `suspend`, `reinstate`,
  `terminate`, `expire`, `cancel`, `amend`.
- Evidence binding — `ApprovalEvidence.content_digest` must equal the digest
  frozen at `propose()`, checked at both `approve()` and `activate()`.
- Optimistic concurrency on every transition (`expected_status`,
  `expected_version`).
- Append-only history, enforced by the `refuse_history_rewrite` trigger against
  every role including `app_admin`.
- Ten versioned facts, enumerated in `PUBLISHED_EVENT_TYPES`.

### Changed from the source implementation

- `pending_approval` is renamed `proposed` (ADR-0057's vocabulary). Safe here
  because this is a greenfield lineage: no deployment has stored the old value
  under `mod_agreements`.
- Approval is EVIDENCE, not a sibling import. The source called
  `vendor_cp.approvals.evaluate(...)` inside `approve()`; a module may not
  import a sibling (ADR-0024) and approvals may not perform the domain's
  transition (ADR-0026 § 6).
- Lines carry opaque `release_ref`/`offer_ref` and caller-frozen terms instead
  of a foreign key to `offer_versions`. Ruling A2(b) detached the offer
  catalogue; ADR-0006 D1 forbids the cross-lineage foreign key.
- `customer_ref` becomes an opaque `counterparty_ref`. ADR-0019 § 1 and ruling
  A3: `vendor_accounts` must not retire into kernel `Party`.
- Amendment and supersession are implemented rather than reserved. The source
  left `superseded_by_id` unset with a note naming it a separable follow-up.
- The append-only history table is new. The source recorded transitions only as
  platform audit events, which a product may purge under its own retention
  policy — an evidence chain that a retention sweep can shorten is not evidence.
