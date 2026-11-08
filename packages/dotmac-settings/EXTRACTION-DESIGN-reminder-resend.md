# Next extraction design — reminder resend transaction fence

Status: scoped 2026-11-08, not built. Promoted to first extraction
after dotmac-settings was parked (kernel `settings_resolver` +
`settings_shadow` own that territory; Academy adopts via ADR-0003
shadow phases post-flip).

## What extracts

`dotmac_academy_v2:app/services/reminder_resend_unit.py` (1,672 lines
at b26c56c) — the transactional fence around a human-triggered resend
of a queued delivery:

- caller-transaction classification (owner / participant / read-only /
  physical-savepoint entries) with refusal before any effect;
- SQL authority admission at before-execute and final-cursor stages
  (raw participant control SQL refused pre-dispatch);
- native flush/rollback abort handling that preserves the selected
  exception and earlier same-root receipts;
- swallowed-commit latching and dirty-target-after-return refusal;
- atomic delivered-log + enqueue + audit receipt under fresh
  authority re-reads (pending authority mutations cannot borrow
  stale proof).

The Academy command layer (`app/services/reminder_resend.py`, web
auth, admin access, reminder declarations) stays in the product.

## Generalization surface (injected, not imported)

- `outbox_model`, `log_model`: SQLAlchemy models for the delivery
  outbox row and the delivered-log row (column accessors for status,
  idempotency key, tenant, payload).
- `authority_models`: the identity/authority mappers whose pending
  state fences the operation (Academy: AuthSession, Party,
  PartyPerson, account profile, Role, PartyRoleGrant, settings).
- `enqueue`: callable performing the product's enqueue inside the
  fence; `audit`: callable writing the product's audit receipt.
- `action`: audit action name; `idempotency_prefix`: receipt key
  namespace.

## Hard constraints carried over

- SQLAlchemy is pinned by the unit's private call-site dependencies;
  the qualified version set is part of the package contract
  (Academy-qualified: 2.0.51; 2.1.8 produced 54 native-flush
  failures and must refuse, not adapt silently).
- Kernel floor follows the identity models consumed (a107 at
  extraction time); no own tables, no lineage.
- Behavior proof = Academy's ported suites
  (test_issue2_reminder_resend_* — 700+ cases) re-pointed at the
  extracted package with zero assertion changes, exactly like the
  Learning extraction.

## Naming

Candidate `dotmac-delivery-resend` (the fence is delivery-shaped, not
reminder-shaped). Confirm against module catalog conventions before
scaffolding; `dotmac-durable-timers` (scheduling) and the kernel
outbox relay (claim/lease/retry) are adjacent but distinct: neither
fences a human-triggered resend inside an arbitrary caller
transaction.
