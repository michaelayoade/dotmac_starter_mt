# dotmac-durable-timers

`dotmac-durable-timers` owns durable timer identity, generation,
supersession, cancellation, trigger acceptance and terminal-history retention.
It is a selectable dual-plane module: each adopting application installs the
tenant plane, the platform plane, or both into its own database.

The module deliberately does not scan for due work or own a dispatcher. A
schedule writes an ordinary kernel outbox event in the caller's transaction
and sets its `available_at` to the requested due instant. The kernel relay owns
claiming, leasing, retry and dead-letter behavior.

All business instants are explicit inputs. The module reads no wall clock and
never commits or rolls back the caller's transaction.
