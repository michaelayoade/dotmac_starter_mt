# Delivery-resend fence: source inventory

Inventory for the `dotmac-delivery-resend` extraction dossier
(`packages/dotmac-delivery-resend/EXTRACTION.toml`). Measured 2026-11-08.

## Qualifying source: Academy

`dotmac_academy_v2@61c072f5ca5fcc34b7fadc211c8272add4519af4`,
`app/services/reminder_resend_unit.py`.

- The unit has exactly three product imports (`EmailOutbox`, `ReminderLog`,
  `AcademyAccountProfile`) plus kernel `AuditEvent`/models. Every product
  surface becomes a `ResendFence` injection (consequence tables, authority
  models, write-phase map); nothing else in the unit is product-shaped.
- The preserved behaviour proof is the four Academy resend suites
  (request-owner, v2 guards, v3 caller transactions, v4 SQL authority),
  re-pointed at the packaged fence with import-line changes only.
- SQLAlchemy 2.1.8 requalification of the source unit produced 54
  native-flush failures. The exact 2.0.51 pin is therefore part of the
  contract, and the package refuses any other runtime at admission.

## Kernel adjacency (not an equivalent)

The kernel has no resend/fence equivalent. `dotmac-durable-timers` owns
scheduling and the kernel outbox relay owns claim/lease/retry; both were
verified adjacent but distinct. Neither fences a human-triggered resend
inside an arbitrary caller transaction.

## ERP: inventoried, no equivalent writer

`dotmac_erp@74fa788ae562905e4c8a1fcd46616585e4bf7694`. A tree-wide search
for resend/reminder units found only sync and HR email resend call sites
(e.g. `app/services/sync/sub/expenses.py`) and reminder task modules. None
classifies a caller transaction, fences participant writes, or pins
receipts. ERP is `inventory_only`: somebody looked; it writes nothing this
package owns.

## Sub: inventoried, no equivalent writer

`dotmac_sub@c7707880232c9298ef89c24a1aea66d35bd3a1e6`. The resend hits are
auth-flow OTP resends (`app/api/auth_flow.py`, `app/schemas/auth_flow.py`)
and route glue. There is no delivery-reminder resend command and no
caller-transaction fence. Sub is `inventory_only` for the same reason.
