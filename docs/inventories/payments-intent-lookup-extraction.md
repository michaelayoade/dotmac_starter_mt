# Payment intent reference lookup prerequisite

Audit date: 2026-10-15. Starter source baseline:
`28d927b9dd4d1e511e9b25927c19b6f6d3c76814`; qualifying Sub source:
`4e2e296f1be63c953cd9b4c7417bacc34e019d9c`.
The explicit `project:dotmac_starter_mt` knowledge search and canonical
`dotmac-billing-cutover-orchestration-2026-10-14` v54 retrieval returned no scope
denial. This is existing Payments owner source work, not publication or adoption.

## Product inventory before implementation

| Qualifying source | Behavior extracted or boundary retained |
| --- | --- |
| Sub `app/services/topup_intents.py:stage_gateway_topup_intent` (952–977) | A natural reference resolves the already stored intent and its pre-provider destination binding. The staging helper is a writer; it cannot substitute for a read query. |
| Sub `app/services/customer_portal_flow_payments.py:create_invoice_payment_intent` (1131) and `verify_and_record_payment` (1324–1335) | Checkout binds invoice/account before provider I/O; verification starts with reference lookup and validates customer ownership. Product authorization remains in Sub. |
| Sub `app/services/payment_provider_events.py` (713–731, 821–836) | Verified provider observations carry provider reference; assembly chooses the existing structural invoice/account binding before settlement. Provider transport and metadata are not payment destination authority. |
| Sub `app/services/payment_reconciliation.py:_target_invoice_id` (461) and `_stage_verified_settlement_for_intent` (662) | Reconciliation requires the explicit stored instruction and validates the candidate/provider before settlement. Allocation and prepaid policies remain product-owned. |
| Starter `dotmac_payments.service:get_confirmed_cash_v1` and `tests/test_payments_isolation.py:test_exact_confirmed_cash_is_fresh_scoped_and_rollback_owned` | Existing typed persisted-scalar query, exact stored precision, tenant scope, no autoflush and caller transaction ownership are the implementation and canary precedent. |

The existing Payments extraction dossier inventories ERP payment-intent ownership
and Sub's qualifying implementation. This additional seam extracts only the
reference lookup needed by callback, verification and reconciliation callers.
It does not add provider transport, receivables, account resolution or allocation.

## Typed observation

`GetPaymentIntentV1(reference)` strips the same natural reference as opening.
`get_payment_intent_v1` requires `TenantScope` and returns `None` alike for
unknown and cross-tenant references. A frozen `PaymentIntentObservationV1`
contains scope, intent ID, reference, payer/target references, purpose, requested
Money using stored code and scale, provider, channel, status and original
opening/expiry/settlement instants. Optional `confirmed` exposes only the intent's
stored confirmed amount; it performs no confirmation query and is not immutable
cash evidence. `get_confirmed_cash_v1` remains the cash-evidence query.

One tenant-and-reference scalar SQL read bypasses dirty ORM identity-map values
and suppresses autoflush. It neither creates a Session nor flushes, commits or
rolls back. It may observe caller-flushed uncommitted state; the caller can roll
it back. Precision is never inferred or repaired. No locking or lifecycle change
is introduced; an observation grants no settlement authority.

## Validation and adoption boundary

PostgreSQL canaries are authored before implementation for full binding,
same-reference tenant separation, unknown/foreign absence, dirty identity-map
freshness/no autoflush, caller rollback, original pending/settled instants and
stored money precision. Focused unit canaries cover public frozen shape, empty
reference/platform refusal and corrupt precision refusal.

Only pinned Ruff static checks are local validation. Hosted CI owns runtime
unit/PostgreSQL and required repository checks; authoring is not execution proof.
No migrations, dependencies, release identity, Sub edits, publication, imports,
production access or writer switch are part of this prerequisite.
