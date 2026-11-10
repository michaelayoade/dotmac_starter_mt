# ADR-0079: Apps publish a resource API, and integrations copy through it

- Status: Proposed (build approved by Michael 2026-11-10: "start 1. The Starter API standard (R0, ADR 0079)"; the R0 ruling is not yet recorded in the register, so this stays Proposed until it is and this record merges)
- Date: 2026-11-10
- Decider: Michael
- Related: ADR-0024 (applications are independent and compose by synchronizing
  data; amended here), ADR-0014 (idempotency), ADR-0020 (billing destination);
  AGENTS.md rules 21 and 28; kernel `machine_auth`, `idempotency`

## Context

ADR-0024 section 1 already requires apps to synchronize through versioned APIs
and webhooks, but it does not say what such an API is. The result is one bespoke
contract per pair of apps. Sub exposes keyset and offset feeds with no per-record
version, no settle horizon and wildcard-expanded scopes; ERP v2 exposes no
machine API at all. An accounting copy made through a bespoke contract can skip
rows, cannot prove completeness, and needs a mapping for every pair.

## Decision

Every Starter app publishes, for the records it owns and for which it is the
single writer, the following. The canonical shapes are the owning Starter
module's own resource schemas (for example the `dotmac-billing` invoice, credit
note, payment-with-allocations and refund, and the `dotmac-party` customer); the
owning domain authors each one (ADR-0024 section 10). Two Starter-composed apps
therefore need no mapping between them. A connector is needed only for a
non-Starter API.

1. **Resource API**, `/api/v1/<resource>`, for every record type the app owns.
   Stable UUID `id`; an integer `version` that increases on every change
   (`updated_at` is metadata, not identity); `status` includes `void`; money is
   decimal strings with a currency; matching references (bank or provider
   reference, channel, certificate numbers) are carried on the record.
2. **Tombstones.** A delete or void is a record, never a missing row:
   `{id, version, deleted: true, deleted_at, updated_at}`. A published resource
   is soft-deleted; the version and `updated_at` advance on the delete.
3. **Changes feed**, `GET /api/v1/<resource>/changes?after=<opaque cursor>&since=&limit=`.
   A keyset over `(updated_at, id)`, never offset paging, including voids and
   tombstones. The cursor is opaque and resource-bound. `since` starts at an
   instant instead of a cursor and is the re-read path (below).
   **What is guaranteed, and what is not.** The feed serves only *settled* rows.
   Settled is bounded by three controls, none of them absolute:
   - The horizon is `clock_timestamp() - settle_horizon` computed in the
     database, never from the reader's clock (default 60 s, minimum 1 s).
   - On PostgreSQL the horizon is also capped at the start of the oldest open
     transaction in the database (`pg_stat_activity.xact_start`, excluding the
     reader) and of any prepared transaction, so a transaction of any length is
     held back until it ends. `pg_stat_activity` shows other roles' sessions
     only as `<insufficient privilege>`; if such sessions exist the reader
     cannot fence them, and the declaration chooses: `refuse` (default, fail
     safe: the call errors rather than claim completeness) or `horizon_only` (an
     explicit choice, reported in the page as `fence`). `pg_read_all_stats` is
     neither required nor to be granted; make the reader role a member of the
     writer roles if it must see them.
   - `updated_at` and `version` of a published resource are stamped by a
     database trigger (`stamping_trigger_ddl`), not by application code. The
     kernel `TimestampMixin` does not qualify: its `onupdate` uses the writer's
     application clock at flush time while inserts use the database `now()`, so
     it breaks the single-clock precondition. A declaration must attest
     `stamped_by_trigger=True`.
   Residuals: a writer the reader cannot see (`horizon_only`) is covered only by
   the hold-back, so a transaction longer than the horizon can still commit
   behind a cursor; batch jobs must commit in batches shorter than the horizon;
   a logical restore or a clock step can move stamps backwards; and an unrelated
   long transaction delays the feed (latency, not loss). The feed is
   at-least-once, and consumers de-duplicate on `(id, version)`. A commit-ordered
   xid watermark would be stronger but breaks on logical restore, so it is not
   the standard.
4. **Completeness summary**, `GET /api/v1/<resource>/summary?from=&to=`: a count
   and a digest of `(id, version)` for the window (bounded, 93 days by default),
   with `settled` false when the window was cut at the horizon and
   `computed_at` from the database clock. A receiver closes a period only on a
   summary computed at close time that is settled and matches its own pairs
   (`verify_period`); on a mismatch it re-reads the window from its start with
   `changes_page(since=window_from)` and checks again. A cached summary never
   closes a period.
5. **Exact-scope read keys** on the kernel machine keys. One key per consumer
   binding; scopes are exact strings with no wildcard (declaration refuses `*`);
   `read`, `contact_read` (email, phone, service address) and `import_write` are
   three distinct scopes. A key holding `import_write` is refused by the feed and
   summary, and a key holding `read` or `contact_read` is refused by the import
   API. Contact fields are removed at any depth of the record. A key's tenant
   must equal the session tenant, and reads are filtered to it. Enforcement
   lives in the helpers as well as the route dependency.
6. **Idempotent import API**, `PUT /api/v1/imports/<resource>/{external_ref}`.
   The ledger key is `(source system, record type, external reference, version)`
   with a content digest. The source system is the authenticated key's
   `source_application`, never the body. Same key and same digest is a no-op
   (`unchanged`); same key with a different digest is `conflict` (refused,
   nothing written, the binding is paused). Closed verdict set: `created`,
   `updated`, `unchanged`, `stale` (a lower version than one imported),
   `conflict`, `blocked:predecessor`, `refused:<code>`. The receiver keeps the
   ledger permanently, so a replay after any receipt is redacted still answers
   `unchanged`. Importers of one reference are serialised by a transaction-scoped
   advisory lock before the prior-version read, so a racing v3 and v5 cannot both
   apply. The stored result is a small flat map of ids only, never a payload or
   personal data. A receiver may pass its own `current_version`; the fallback
   ledger prefix scan is a filter on the tenant index, not an index range.
7. **Versioning.** Additive changes stay in v1; a breaking change publishes v2
   beside v1 (ADR-0024 section 11); callers pin a contract digest (section 13).

**The Integrator is a generic API-to-API sync engine.** A binding is a POLL on a
source changes feed, a canonical observation, and a DELIVERY to the destination's
import API. Formats are the Starter modules' own schemas; the engine owns the
cursor, retries, receipts and retention.

## The kit

`dotmac_kernel.resource_api` and `dotmac_kernel.source_import` carry the
plumbing so that a module adopts the standard by declaring a
`ResourceDeclaration` (name, model, column names, serializer, `ResourceScopes`,
contact fields, settle horizon) rather than writing it: cursor codec, changes
page, tombstone envelope, summary digest, scope checks, and the import verdict
helper. The import ledger is the existing kernel idempotency ledger
(`scope = import:<source>:<type>`, `key = <ref>@<version>`,
`fingerprint = digest`, no expiry); no table or migration is added.

## Consequences

- No module adopts the standard in this change; adoption is per module in later
  slices, and each adopter owns its schemas and contract digest.
- A resource that hard-deletes rows cannot be published until it soft-deletes.
- The feed trails by the horizon, and by any open transaction, in exchange for
  completeness; the guarantee and its residuals are stated in section 3.
- Published tables need the stamping trigger in their module migration.
- The kit does not provide HTTP routing; adopters mount the helpers behind
  `ResourceDeclaration.read_dependency()` and `import_dependency()`.
- Legacy applications are not changed by this ADR.
