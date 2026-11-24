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

## Job runner (0.1.0a3)

Modules declare recurring work as `JobSpec`s on `ModuleManifest.jobs`. This
module runs them: `python -m dotmac_durable_timers.run --assembly module:attr`
is a second process from the same image. It uses two credentials, the
`outbox_dispatcher` role for claim and settle only and `app_user` for every
tenant read and every handler (inside the event tenant's RLS session). A job
is a timer stream; its generation is the idempotency key. A handler is
`handler(session, scope, run_context) -> JobOutcome` and must be idempotent.
Failures retry with the job's backoff, then dead-letter through the relay with
an audit row and a health observation (the assembly binds a `JobHealthSink`
under `providers["durable_timers.health_sink"]`). There is no staff queue.
