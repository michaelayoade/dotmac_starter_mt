# Complimentary and sponsored subscription treatment extraction

**As of:** 2026-08-23
**Status:** product-first audit complete; implementation waits for the
registry-verified `dotmac-subscriptions 0.1.0a2` release and begins as a separate
`0.1.0a3` / `su_0002` slice.
**Qualifying source:** `dotmac_sub` at
`943bc59f8e4ca0849c7de578bc9dbc17c57b116f`.

## Decision

The reusable owner is `dotmac-subscriptions`. A complimentary or sponsored
service is never represented by a zero catalogue price or zero contract line.
The immutable contract line retains its actual strictly positive price. An
effective-dated arrangement records approval not to collect that amount from
the customer, and an append-only grant records the exact non-cash amount and
service period applied under that approval.

Sub is the mandatory product-first source. No Starter module or other product
in the audited scope owns an equivalent lifecycle. Sub's
`SubscriptionBillingArrangement` and `SubscriptionBillingGrant` already prove
the core invariants:

- treatment is `complimentary` or `sponsored`; standard billing is the absence
  of an effective arrangement;
- the seven reasons are `internal_service`, `staff_benefit`,
  `partner_service`, `community_support`, `commercial_concession`,
  `sponsored_service`, and `other_approved`;
- every arrangement has a mandatory end and snapshots the approval horizon;
- the positive maximum recurring amount, currency and cadence are frozen as
  approval evidence;
- sponsored service requires an opaque sponsor or cost-centre reference;
- approval and revocation carry actor, reason, command, correlation,
  idempotency and fingerprint evidence;
- overlapping active arrangements fail closed;
- a price, currency, cadence, offer or account mismatch becomes protected
  drift: customer charging remains suppressed, but a grant cannot be
  fabricated;
- every grant is positive, bounded by the approved and contracted amount,
  exact-period, idempotent and append-only; and
- changing commercial terms while an arrangement is open requires revocation
  and reapproval.

## Exact source inventory

| Source | Disposition |
|---|---|
| `app/models/subscription_billing_treatment.py` | Port treatment/reason/status vocabulary and arrangement/grant evidence. Replace Sub FKs with module-owned contract, line, offer-version and occurrence identities. |
| `app/services/subscription_billing_treatments.py` | Port preview fingerprinting, finite/aligned interval checks, overlap refusal, approval/revocation replay, effective resolution and protected-drift semantics. |
| `app/services/subscription_billing_grants.py` | Port positive bounded grant validation, natural identity, replay conflict detection and append-only evidence. Split its Sub entitlement/anchor/event consequences into adopter outputs. |
| `alembic/versions/399_subscription_billing_treatments.py` | Port checks, uniqueness, append-only grant trigger and the commercial-term freeze invariant. Do not port public-schema names, product FKs, permission seeding or Sub enum types. |
| `docs/designs/SUBSCRIPTION_BILLING_TREATMENTS.md` | Preserve the decision that zero price hides foregone revenue and that grants never create customer money. |
| `tests/test_subscription_billing_treatments.py` | Preserve approval replay, exact grant replay, no-zero fallback, revocation race, finite/aligned period, approval horizon, positive-price drift, sponsor evidence and no-customer-money cases. |
| `tests/test_subscription_billing_treatment_api.py` | Preserve fingerprint-bound preview/confirm behavior as package service contracts; HTTP and permissions stay in Sub. |
| `tests/architecture/test_subscription_billing_treatment_ownership.py` | Preserve one-writer, flush-only grant, append-only evidence, positive value and term-freeze guard shapes with sensitivity proofs. |

## Target package contract

The package adds the same two tables to each selected persistence plane:

1. `subscription_billing_arrangements` /
   `platform_subscription_billing_arrangements` bind one stable
   `subscription_contract_id` plus `contract_line_key` to the exact authorized
   contract version and offer version. They snapshot treatment, reason, finite
   interval, policy reference/version and maximum days, positive maximum amount,
   currency/scale, cadence, sponsor/cost-centre references, approval fingerprint
   and revocation evidence.
2. `subscription_billing_grants` / `platform_subscription_billing_grants` bind
   one arrangement to one exact recurring occurrence, contract line and
   half-open service period. They retain positive reference amount,
   currency/scale, actor/reason and deterministic command/correlation evidence.

The public service surface will contain product-neutral commands and outputs:

- `PreviewBillingArrangementCommand` validates the current immutable contract
  line, cadence, approval policy and interval and returns a content fingerprint;
- `ApproveBillingArrangementCommand` accepts that fingerprint and writes one
  replay-safe arrangement;
- `RevokeBillingArrangementCommand` records prospective revocation without
  changing old grants;
- `resolve_billing_arrangement` returns `standard`, `effective`, or
  `protected_drift` and never silently selects overlapping evidence;
- `RecordNonCashGrantCommand` records one bounded append-only grant against the
  exact line occurrence; and
- `NonCashGrantOutputV1` carries the arrangement, line, period, treatment,
  reason and exact foregone amount for assembly adapters.

All money uses `ExactAmount`; floats and zero values are refused. Arrangement
and grant functions accept `TenantScope` or `PlatformScope`, use the caller's
session, mutate and flush, and never import Billing, Accounting, Entitlement,
Sub, Vendor CP or an assembly. The product supplies the effective approval
policy reference/version and maximum-days value. Sub's existing registered
setting keeps its one-to-366-day bound; the package records and enforces the
supplied snapshot without reading product settings.

`su_0002` must:

- create both new tables on every supported selected plane with the existing
  plane's isolation contract;
- freeze grants against update/delete in PostgreSQL;
- refuse an overlapping contract-version insertion while an arrangement is
  open, so a plan/price change requires revocation and reapproval;
- use composite same-plane FKs only; and
- leave the released `su_0001` bytes unchanged.

## Product consequences that do not port

| Consequence | Owner after extraction |
|---|---|
| Resolve Sub's current offer/unit price during backfill | Sub adapter until the contract-line cutover; then the module contract line is authoritative |
| Create/repair `ServiceEntitlement` and advance the ISP billing anchor | Sub's thin subscription-access adapter consuming `NonCashGrantOutputV1` |
| Suppress invoice/customer-money creation | Sub/Billing assembly mapping from the effective arrangement or grant; Billing remains receivable owner |
| Sponsor receivable, internal expense, cost-centre allocation and foregone-revenue posting | Accounting through a typed consequence adapter; no posting logic enters subscriptions |
| Permission, settings, audit and product outbox declarations | each adopting assembly, using the package command/output contracts |
| Customer/account/subscriber identity | Sub/Customer owner; the package stores only opaque product link evidence where needed |

## Migration and cutover gates

1. Release and registry-verify subscriptions a2 first. Do not silently widen
   the unreleased candidate and claim two separately reviewable slices were one.
2. Implement a3 canary-first from the exact sources above and prove tenant,
   platform and dual-plane isolation plus an a2-to-a3 migration rehearsal.
3. In Sub, backfill only cases with a provable positive contracted value,
   treatment reason, approver, finite end, policy evidence and sponsor evidence
   when applicable. A legacy zero row or disabled billing flag proves none of
   those facts and requires operator adjudication.
4. Shadow effective treatment, grant amount/period, invoice suppression,
   entitlement, billing anchor and foregone-revenue outputs. Unexplained drift
   blocks cutover.
5. Switch the arrangement/grant writer to the exact module release. Sub retains
   only its typed entitlement/access consequence; Accounting receives its typed
   consequence independently.
6. Prohibit new zero catalogue/reference/contract-line prices as a concession
   mechanism, migrate references, and retire the local arrangement/grant tables
   and writers only after historical evidence remains reachable.

`contract_consumers` remains unchanged until the local owner is actually
retired. Release, composition and a shadow run are evidence, but none alone is
adoption.
