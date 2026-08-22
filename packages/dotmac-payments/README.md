# dotmac-payments

Owns the **intent to pay** and the **correlation** between that intent and the
external fact that it was paid — a provider callback, a reconciliation row, a
reviewed bank-transfer proof, or a manual entry.

It does not own receivables. Billing decides what a confirmed payment settles
and in what order; Banking owns accounts; Integrator owns provider transport.

Two invariants are the point of the module:

- **The destination is bound before provider I/O.** An external settlement fact
  is correlated to an intent addressed by that intent's own reference. Provider
  metadata corroborates; it never selects which intent gets credited.
- **Money is exact and currency-checked.** Amounts are `Numeric`, never float,
  and a confirmation in a different currency from its intent is refused rather
  than coerced.

The tenant-only `pm` lineage owns `mod_payments`.
