# dotmac-durable-timers

`dotmac-durable-timers` owns durable timer identity, generation,
supersession, generation-aware cancellation, trigger acceptance/rejection
evidence, opaque source-version carry and terminal-history retention.
It is a selectable dual-plane module: each adopting application installs the
tenant plane, the platform plane, or both into its own database.

The module deliberately does not scan for due work or own a dispatcher. A
schedule writes an ordinary kernel outbox event in the caller's transaction
and sets its `available_at` to the requested due instant. The kernel relay owns
claiming, leasing, retry and dead-letter behavior.

The consuming module owns and declares each output event type on its manifest;
scheduling refuses an undeclared code before it touches the database. The
package exports typed lifecycle/read ports and does not export its ORM models.

All business instants are explicit inputs. The module reads no wall clock and
never commits or rolls back the caller's transaction.

## Integration checklist

- Pin `dotmac-durable-timers` in the consuming assembly and register `dotmac_durable_timers.module` there.
- Bind the prerequisites declared by the manifest: `MODULE_DATABASE_ROLES_V1.name`, `OUTBOX_RELAY_V1.name`.
- Add `dotmac_durable_timers.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: proof 1 concurrent first schedules allocate gapless generations](../../tests/test_durable_timers_isolation.py#L276). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_durable_timers_isolation.py::test_proof_1_concurrent_first_schedules_allocate_gapless_generations -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
