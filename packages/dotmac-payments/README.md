# dotmac-payments

Owns the **intent to pay** and the **correlation** between that intent and the
external fact that it was paid — a provider callback, a reconciliation row, a
reviewed bank-transfer proof, or a manual entry.

It does not own receivables. Billing decides what a confirmed payment settles
and in what order; Banking owns accounts; Integrator owns provider transport.

Three invariants are the point of the module:

- **The destination is bound before provider I/O.** An external settlement fact
  is correlated to an intent addressed by that intent's own reference. Provider
  metadata corroborates; it never selects which intent gets credited.
- **Money is exact and currency-checked.** Amounts are `Numeric`, never float,
  and a confirmation in a different currency from its intent is refused rather
  than coerced.

- **Confirmation replay preserves evidence.** A repeated provider and external
  reference retains its intent, amount, currency, source and observation instant.
  Explicitly changed evidence conflicts; an omitted observation instant reuses
  the stored one. Intent mutations serialize on the intent row. Transfer-proof
  review locks that intent before its proof and records acceptance only after
  the shared confirmation path succeeds.

Version `0.1.0a2` is declared and unpublished. Sub remains the live writer until
its history backfill, zero-drift shadow and separately authorized cutover pass.

The tenant-only `pm` lineage owns `mod_payments`.
