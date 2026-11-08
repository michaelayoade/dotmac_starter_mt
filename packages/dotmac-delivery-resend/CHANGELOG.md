# Changelog — dotmac-delivery-resend

## 0.1.0a1 — UNRELEASED

Source only: not released, not published, not adopted.

- Ported from `dotmac_academy_v2:app/services/reminder_resend_unit.py`
  at Academy `61c072f5ca5fcc34b7fadc211c8272add4519af4`. Behavior is
  byte-identical except the reviewed deltas below.
- Reviewed deltas (the complete semantic diff):
  - Academy/kernel model imports replaced by the injected
    `ResendFence` (consequence tables, authority models, write-phase
    map). The unit never called the product's enqueue/audit — those
    run in the command layer inside the fence.
  - `admit()` phase gating is a lookup in the fence phase map instead
    of hardcoded enqueue/audit table identities.
  - `remember_rollback(tenant_id, prior)` takes a
    `{model: rows}` mapping instead of positional per-table dicts.
  - `prepare_resend(db, fence)` / `pending_authority(db, fence)` /
    `authority_preflight(db, fence)` accept an explicit fence, falling
    back to the active unit's fence or the configured default fence.
  - `ReminderUnit.__init__` takes the fence; a second fence against an
    active session is refused.
- Pins: `sqlalchemy ==2.0.51` exact, `dotmac-kernel >=0.1.0a107`.
