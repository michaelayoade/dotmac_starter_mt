# dotmac-payables

`dotmac-payables` owns tenant supplier invoices, credit notes, recognized
liabilities, due-date payment obligations, credit applications and immutable
settlement/accounting observations.

Version `0.1.0a7` is an unpublished candidate and is not adopted by this change.
It makes separation of duties on supplier payments an optional per-tenant policy
(D-9; Michael's ruling that two-person approval is never compulsory). Prepare,
approve, record and reverse take a keyword-only `separation_of_duties` (default
`False`: separation is OFF by default, per Michael's no-compulsory-two-person ruling;
a caller that wants two-person approval passes `True`, and assemblies pass the tenant
policy explicitly). `create_supplier_payment` stamps the policy in force on the row
(`separation_enforced`, immutable, migration `pa_0007`) and the later steps enforce
`stamp or parameter` (the stricter value wins). The database
`ck_supplier_payments_sod` check relaxes per row, and only when that row's stamp is
false.

Version `0.1.0a6` added the cash-basis release of deferred input tax at payment (a declared
`TaxDeferralPolicy`, proportional with an exact final remainder, reversed with the
payment), verifier-bound no-GL admission of the deferral already released on settled
historical invoices and of historical overpayments as supplier credits, withholding
account guards, and zero-value line handling in the document consequences. No second
person is required by any of it.

Version `0.1.0a5` added a governed reversal of RECORDED supplier payments (REVERSED, terminal; the
reverser is not the recorder or approver; obligations reopen; a GL reversal
consequence is exposed), verifier-bound admission of partially settled historical
invoices with immutable legacy settlements and an exactly proved residual (no GL
entry), and `recognize_approved_invoice` for imported APPROVED invoices that never
reached the owner ledger.

Version `0.1.0a4` added a governed supplier-payment lifecycle (DRAFT, APPROVED, RECORDED or
CANCELLED) with preparer, approver and recorder kept distinct, settlement of open
obligations in the caller's transaction, and a provider-neutral accounting
consequence (Dr supplier liability, Cr cash, optional Cr withholding tax) that an
assembly posts and then acknowledges with `record_accounting_receipt`. RECORDED
asserts that money left through an external channel; payables moves no money,
calls no provider and writes no ledger.

Version `0.1.0a3` added `admit_historical_unpaid_invoice` for an already migrated
APPROVED invoice whose trusted assembly verifier establishes a historical
POSTED, wholly unpaid STANDARD source and exact imported accounting evidence.
It creates one full-face OPEN obligation, the original recognition event, and
an immutable permanent receipt. It preserves invoice fields and nullable
original actors; no new approval or Accounting consequence is produced.

The assembly injects `HistoricalUnpaidInvoiceVerifier`; callers cannot replace
verification with an import flag. Only a fixed, trusted read-only verifier is
supported. Admission rejects pending ORM writes and detects an ended savepoint;
`Session.commit()` is refused before it can commit the caller's work. The verifier
runs in a discarded savepoint. Callers must roll back their entire transaction
on any refusal; a verifier calling `Session.rollback()` can already abort it.
This is not a sandbox for direct Connection or raw SQL/DBAPI transaction control, advisory locks,
sequence `nextval`, or effects outside the database.
This package trusts the evidence the verifier returns. The ERP verifier
implementation does not exist yet and needs its own architect review before
any ERP call site is enabled; each assembly must supply and review its concrete
verifier.
Commands bind source identity, frozen snapshot
SHA-256, destination invoice and complete cv1 evidence fingerprint. Permanent
receipts preserve replay after kernel key retention and later real settlement.
Changed evidence conflicts. Supplier-issued document numbers may remain NULL
when historically absent; new document creation still requires them.

It does not own supplier identity, procurement, approval decisions, tax policy,
inventory, Accounting journals, bank details or payment execution; the payment
record carries only opaque account and instrument references. An assembly
translates its provider-neutral accounting consequence and supplies settlement
facts from the payment owner. The package owns `mod_payables` through the
independent `pa` lineage and never commits or rolls back the caller session.

See `EXTRACTION.toml`, ADR-0042 and
`docs/inventories/accounting-payables-sources.md` for the product-first
boundary.
