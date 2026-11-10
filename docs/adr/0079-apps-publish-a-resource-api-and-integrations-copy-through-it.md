# ADR-0079: Apps publish a resource API, and integrations copy through it

- Status: Accepted (Michael, 2026-11-10: "start 1. The Starter API standard (R0, ADR 0079)"; effective when this record merges)
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
3. **Changes feed**, `GET /api/v1/<resource>/changes?after=<opaque cursor>&limit=`.
   A keyset over `(updated_at, id)`, never offset paging, including voids and
   tombstones. The cursor is opaque and resource-bound.
   **Late commits are not skipped by a settle horizon (hold-back window):** the
   feed serves only rows with `updated_at` older than `now - horizon`. PostgreSQL
   `now()` is the transaction start time, so a slow transaction commits "in the
   past"; the horizon must exceed the longest write transaction on the resource
   and `updated_at` must be written from one clock. A commit-ordered sequence
   would remove that assumption but needs storage every resource does not have,
   so the horizon is the standard method. Consumers treat the feed as
   at-least-once and de-duplicate on `(id, version)`; an overlapping re-read is
   always safe.
4. **Completeness summary**, `GET /api/v1/<resource>/summary?from=&to=`: a count
   and a digest of `(id, version)` for the window, with `settled` false when the
   window was cut at the horizon. A receiver closes a period only on a settled
   summary that matches its own.
5. **Exact-scope read keys** on the kernel machine keys. One key per consumer
   binding; scopes are exact strings with no wildcard (declaration refuses `*`);
   `read`, `contact_read` (email, phone, service address) and `import_write` are
   three distinct scopes; a key that can write imports cannot read. Enforcement
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
   `unchanged`.
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
- The horizon trades latency (the feed trails by the horizon) for completeness.
- The kit does not provide HTTP routing; adopters mount the helpers behind
  `ResourceDeclaration.read_dependency()` and `import_dependency()`.
- Legacy applications are not changed by this ADR.
