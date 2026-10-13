# Tax authoring inside a registered transaction owner

Inventory recorded before this implementation, 2026-10-13. The source baseline
is Tax policy candidate `c850a12f99549979f738349188a0d86b530afb59`.

Tax already owns natural-key normalization, exact-content replay comparators,
catalog creation and immutable rule publication in
`packages/dotmac-tax/src/dotmac_tax/service.py`. Its existing `ensure_tax_authority`,
`ensure_tax_jurisdiction`, `ensure_tax_code` and `ensure_tax_rule` resolve insert
conflicts inside `dotmac_kernel.transactions.conflict_savepoint`. This completes
a nested transaction, so these commands cannot participate unconditionally in
Sub's registered transaction-owner boundary, which rejects participant
completion through real Session events. A preliminary public read cannot make
the existing ensure safe: authority, jurisdiction and code catalogs allow
mutation/deletion, so a concurrent catalog change can enter its missing branch.

The bounded extraction is four explicitly public `ensure_tax_*_in_transaction`
commands with the existing typed inputs, explicit tenant IDs and ORM-free V1
results. They reuse Tax normalization, exact-content comparators and creation/
publication helpers. They perform no commit, rollback, nested transaction or
internal database-error recovery. A unique-insert loser propagates `IntegrityError`;
the caller rolls back and retries its entire owner command in a fresh transaction.
The retry either replays exact content or raises `TaxConflict`.

Authority/rule creation helpers also have a duplicate precheck. If a winner
commits between the participant's first lookup and that precheck, the helper
raises a domain `TaxConflict` before adding or flushing rows. The participant
may re-read that healthy transaction and exact-replay the winner, or reject
different content. The helper's domain-conflict paths must remain before all
mutations. Session-health/pending-work checks provide additional refusal but
cannot prove that hypothetical future code has not already flushed writes.
No database integrity failure is caught or recovered inside the participant.

Catalog replay means equality in the current transaction's policy view; it is
not durable idempotency after catalog changes. Published rule versions remain
immutable, and unpublished rules cannot be returned as an exact replay. No new
schema, rate calculation, Sub ORM import, copied comparator or product writer
switch is part of this change. Existing released writer signatures and
savepoint conflict-recovery behavior remain supported.

The new participants explicitly flush pending owner work before refreshed
catalog reads, so a dirty local catalog edit is persisted rather than replaced
by a refresh. The caller's `Session.autoflush` boolean remains unchanged.
Refreshed parent reads also prevent stale parent status/currency from silently
admitting a new child or altering the meaning of a rule DTO. This does not
promise a database isolation level stronger than the caller's transaction.

Source canaries will cover first authoring, exact replay, content drift,
cross-tenant parents, Session completion guards, insert races with whole-owner
retry, and failure after flush with complete owner rollback. Fresh migrated
PostgreSQL app-user canaries remain hosted-CI work. Source/static inspection is
not runtime evidence or Sub adoption.
