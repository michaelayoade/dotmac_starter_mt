# dotmac-delivery-resend

Transactional fence for a human-triggered resend of a queued delivery,
executed inside an arbitrary caller transaction.

Extracted from Academy's reminder-resend unit
(`dotmac_academy_v2:app/services/reminder_resend_unit.py`), the only
qualified consumer at 0.1. The fence classifies the caller transaction
(owner / participant / read-only / physical-savepoint entries) and
refuses before any effect; admits SQL authority at before-execute and
final-cursor stages (raw participant control SQL is refused
pre-dispatch); survives native flush/rollback aborts preserving the
selected exception and earlier same-root receipts; latches swallowed
commits; and pins the delivered-log + enqueue + audit receipt under
fresh authority re-reads so pending authority mutations cannot borrow
stale proof.

The product injects a `ResendFence`: its consequence tables (each with
`id` + `tenant_id`), its authority models, and a write-phase map naming
which tables may be written in each phase. Authority checks, enqueue,
rendering, and audit writes stay in the product command layer and run
inside the fence.

Hard constraints (see COMPATIBILITY.md): SQLAlchemy ==2.0.51 exactly,
postgresql+psycopg only — refused at runtime otherwise; kernel
>=0.1.0a107 for the conflict-savepoint transaction API. No tables, no
lineage, no engine.

Behaviour proof is Academy's ported resend suites
(`dotmac_academy_v2:tests/test_issue2_reminder_resend_*.py`) re-pointed
at this package with import-line changes only.
