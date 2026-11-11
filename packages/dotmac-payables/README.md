# dotmac-payables

`dotmac-payables` owns tenant supplier invoices, credit notes, recognized
liabilities, due-date payment obligations, credit applications and immutable
settlement/accounting observations.

Version `0.1.0a3` is an unpublished candidate and is not adopted by this change.
It adds `admit_historical_unpaid_invoice` for an already migrated
APPROVED invoice whose trusted assembly verifier establishes a historical
POSTED, wholly unpaid STANDARD source and exact imported accounting evidence.
It creates one full-face OPEN obligation, the original recognition event, and
an immutable permanent receipt. It preserves invoice fields and nullable
original actors; no new approval or Accounting consequence is produced.

The assembly injects `HistoricalUnpaidInvoiceVerifier`; callers cannot replace
verification with an import flag. Admission refuses, fail-closed, if the
verifier leaves any pending write on the session (it must only read); the
verifier runs in a discarded savepoint. Callers must roll back the session on
any refusal. This
package trusts whatever evidence the verifier returns: the ERP verifier
implementation does not exist yet and needs its own architect review before any
ERP call site is enabled. Commands bind source identity, frozen snapshot
SHA-256, destination invoice and complete cv1 evidence fingerprint. Permanent
receipts preserve replay after kernel key retention and later real settlement.
Changed evidence conflicts. Supplier-issued document numbers may remain NULL
when historically absent; new document creation still requires them.

It does not own supplier identity, procurement, approval, tax policy,
inventory, Accounting journals, bank details or payment execution. An assembly
translates its provider-neutral accounting consequence and supplies settlement
facts from the payment owner. The package owns `mod_payables` through the
independent `pa` lineage and never commits or rolls back the caller session.

See `EXTRACTION.toml`, ADR-0042 and
`docs/inventories/accounting-payables-sources.md` for the product-first
boundary.
