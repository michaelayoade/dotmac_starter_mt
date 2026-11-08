# Compatibility — dotmac-delivery-resend 0.1.0a1

- **SQLAlchemy ==2.0.51, exact.** The fence depends on private 2.0.51
  call sites (native `_flush` identity, transaction ancestry, dialect
  dispatch sentinels). 2.1.8 changed those internals and produced 54
  native-flush failures in Academy's requalification attempt; the unit
  therefore refuses any other version at runtime rather than adapting
  silently. A new qualified version is a reviewed requalification:
  pin bump here plus the full Academy resend suites green.
- **Dialect postgresql+psycopg only**, non-autocommit, non-twophase,
  no `schema_translate_map` — refused at runtime. `psycopg` itself is
  the adopter's driver dependency, not this package's.
- **dotmac-kernel >=0.1.0a105.** Floor set by the
  `dotmac_kernel.transactions` conflict-savepoint API consumed
  (`ConflictSavepointPhase`, `claim_conflict_savepoint_commit`,
  `conflict_savepoint_requires_rollback`, `current_conflict_savepoint`),
  first published together in a105. Academy pins a107 exactly, which
  satisfies the floor.
- **Consequence tables must be tenant-shaped** (`id` + `tenant_id`
  columns). Platform-plane (tenant-less) consequences are out of the
  0.1 contract.
- **Symbol stability in 0.1**: `ReminderResendError`, `ReminderUnit`
  (including `patch`, `ended/failed/corrupt`), `pending_authority`,
  `_unit`, and all refusal message strings are preserved verbatim from
  the Academy source so the behavior suites re-point with import-line
  changes only. Renaming is a later major decision.
- Importing this package registers process-global SQLAlchemy `Session`
  event listeners (inert without an active unit). Never load it
  alongside a second copy of the fence in one process: listeners would
  double-fire. Adoption deletes the product-local unit in the same
  change; no shim.
