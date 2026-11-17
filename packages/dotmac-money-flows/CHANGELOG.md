# Changelog — dotmac-money-flows

## 0.1.0a1 — unreleased

Source only; not published, not composed into any assembly.

- Tenant-plane manifest (`money_flows` / `mflows` / `mod_mflows` / `mf`), the
  instance-table allowlist and the Settle outbox event types.
- Migration `mf_0001` creates `settle_instances` with forced row-level security,
  no DELETE grant for the runtime role and a trigger that holds the whole
  transition graph: insert only as settled, refused or unapplied at version 0;
  refused to refused, settled or unapplied; unapplied to resolved only; settled
  and resolved terminal; every update advances `version` by exactly one; identity
  columns and an accepted `settlement_id` never change. A resolved row keeps its
  settlement and reason code.

- F1 Settle: `settle_confirmation`, `record_settle_refusal`, `retry_settle`,
  `get_settle_instance` and `list_settle_attention`, with the typed contracts, the
  `SettlementTargetResolver` and `SettlePolicyReader` ports and the
  `SettleRefused` error. Everything runs in the caller's transaction; no commit,
  rollback, savepoint, clock or timer. The row is written once at its outcome and
  a replay returns it with no owner call. The target comes only from the payment
  intent. Manual entry and transfer proof are off by policy default.
- Posting instant: Billing's claim and postings use the Payments cash
  observation's `observed_at`; the settle command carries no caller instant.
- `list_settle_attention` pages by a `(created_at, id)` keyset cursor.
- Depends on `dotmac-billing >=0.1.0a12` and `dotmac-payments >=0.1.0a4`.
- A database test applies the schema vocabulary rule to the live `mod_mflows`
  catalog.

### Before release

- Measure the oldest-due-first page walk of `list_open_receivable_documents`
  under the account lock on an ST-4-scale account (many open invoices). No such
  fixture exists in the repository yet (the opening scale fixture is a census,
  not an account's open documents), so none was built here.
- Raise the kernel floor to the kernel release that carries the `money_flows`
  migration-namespace reservation.

- Migration `mf_0002` creates `funded_charge_instances` (F2 FundedCharge) with
  forced row-level security, no DELETE grant and a guard trigger: insert only as
  pending, discharged or attention_required at version 0; pending to discharged,
  attention_required or withdrawn; attention_required to attention_required,
  pending, discharged or withdrawn; discharged and withdrawn terminal; version
  advances by exactly one; identity columns and recorded discharge references
  never change. There is no deadline, timer or cancellation: `deadline_at` and
  `resolution_ref` exist only as common columns and CHECKs pin them NULL.
  `mf_0001` is unchanged. No flow behaviour yet.
