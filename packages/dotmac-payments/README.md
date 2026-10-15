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


Currency precision is persisted as `minor_units` alongside the code on intents,
proofs and confirmations. New commands require finite positive amounts that fit
`Numeric(20, 6)` and an explicit Money scale from 0 through 6. A code with a
different scale is a different currency for replay and correlation.

`get_confirmed_cash_v1(db, scope=TenantScope(...), query=GetConfirmedCashV1(id))`
returns frozen `ConfirmedCashObservationV1` with `scope`, `confirmation_id`,
`intent_id`, exact `confirmed: Money`, `source`, `provider_type`,
`external_reference` and the original `observed_at`. It reads persisted immutable
confirmation scalars without autoflushing pending ORM changes or completing the
transaction. `ConfirmationNotFound` refuses absent/cross-tenant IDs;
`UnresolvedMoneyPrecision` refuses historical evidence whose scale is unknown.
A caller may observe its own uncommitted confirmation; the caller owns commit
and rollback, so an observation is not a durable transaction receipt. Assembly
must resolve intent/payer to account through its own structural binding; provider
metadata never supplies Billing identity or turns claimed deductions into cash.

`get_payment_intent_v1(db, scope=TenantScope(...),
query=GetPaymentIntentV1(reference))` reads the stored intent by its stripped
natural reference. It returns frozen `PaymentIntentObservationV1` with scope,
intent ID, reference, payer/target references, purpose, requested Money, optional
stored confirmed Money, provider/channel, status and opening/expiry/settlement
instants. Unknown and cross-tenant references return `None`; platform scope and
empty references are refused. Persisted scalar reads ignore dirty ORM values,
suppress autoflush and leave commit/rollback to the caller. Stored precision is
never inferred and noncanonical currency codes are refused. The optional
confirmed amount describes intent state; immutable cash evidence still comes
from `get_confirmed_cash_v1`. Neither observation authorizes settlement or
resolves a Billing account. See the [lookup inventory](../../docs/inventories/payments-intent-lookup-extraction.md).

Additive `pm_0002_money_precision` leaves existing rows with NULL precision. It
has no scale default or inferred backfill: neither a currency registry nor the
Numeric decimal exponent proves the original command scale. Legacy proof
acceptance and monetary correlation fail closed. Cancellation, expiry and proof
rejection retain their existing lifecycle behavior. Resolving historical scales
requires separate source evidence and authorization; this package supplies no
repair writer.

Additive `pm_0003_confirmation_immutable` makes confirmation UPDATE and DELETE
fail at the PostgreSQL boundary, including direct SQL, no-op updates and intent
or tenant deletion cascades. `app_user` retains SELECT/INSERT but loses
UPDATE/DELETE on confirmations; owner-issued DML is refused by the trigger.
Intent/proof lifecycle privileges and tenant RLS stay intact. This protects
ordinary DML; database owners and superusers still control DDL and can remove
triggers. It does not attest privileged administration.

The migration takes a transaction-scoped exclusive table lock with a 5-second
lock timeout and 60-second statement timeout, failing atomically on contention.
It does not rewrite confirmation rows or resolve legacy NULL precision. Empty
pre-adoption databases support downgrade to `pm_0002_money_precision` and
reupgrade; downgrade refuses while any confirmation evidence exists, including
legacy evidence, before removing protections. Before adoption, source rollback
is available without applying this migration. After adoption, retain financial
evidence and the database guard when rolling application source back; production
migration downgrade is not a rollback procedure. PostgreSQL mutation, populated
predecessor, lifecycle race/replay and caller rollback canaries require hosted
CI acceptance; authoring them is not execution evidence.
