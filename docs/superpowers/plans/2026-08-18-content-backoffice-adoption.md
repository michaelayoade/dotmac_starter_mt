# `dotmac-content` extraction and Backoffice adoption plan

**Status:** audit and RED-canary slice prepared; no package implementation or
adoption has started.
**Source pin:** Mkt `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d`,
verified directly against remote `main` on 2026-08-18.
**First adopter:** Backoffice. **Second candidate:** Sub, independently.

## Outcome

Ship one tenant-only `dotmac-content` module that owns editorial plans,
canonical content items, provider-neutral variants, editorial calendar
placement and creative-file references. Backoffice adopts the released module,
migrates selected Mkt data, switches each editorial writer and deletes the old
writer. The slice is incomplete while Mkt and the module can both decide the
same fact.

## Non-negotiable boundary

- Backoffice owns actor authentication, permissions and collaboration policy.
- `dotmac-files` owns stored bytes; content stores opaque file references.
- `dotmac-publishing` owns targets, requested publication time, immutable
  release snapshots and reconciled publication state.
- `dotmac-campaigns` owns audiences, recipients, suppression checks, attempts
  and outcomes.
- Integrator owns credentials, OAuth, provider code, webhooks, retries,
  checkpoints and transport evidence.
- Generic tasks remain with the independently selected project/work owner.
- Modules do not import siblings. Applications do not share databases, ORM
  models, sessions, filesystems or writers.

## Gate 0 — freeze the evidence

1. Recheck that Mkt `main` still resolves to the accepted source pin immediately
   before the first code port. If it moved, characterize the selected content
   paths and update the full revision in `EXTRACTION.toml` and the dossier.
2. Review the four intentional corrections: `Campaign -> ContentPlan`,
   `planned -> ready`, `published` moves to publishing, and membership remains
   Backoffice authorization.
3. Keep all nine writer rows in
   `docs/inventories/content-writer-retirement.toml` at `not-started`.

**Exit:** one accepted source coordinate and no unresolved ownership term.

## Gate 1 — prove the canaries fail first

1. Commit only the audit, dossier, retirement ledger and the two content
   canary files.
2. On Observer, create a fresh isolated writable worktree pinned to that exact
   commit. Run the focused tests with no more than three workers.
3. Record the expected RED: the `dotmac_content` distribution and its
   `mod_content` contract do not exist. A passing or skipped canary is a defect.

**Exit:** preserved RED output tied to the exact commit. No runtime code lands
before this evidence.

## Gate 2 — implement the smallest owning module

1. Allocate owner `content`, short code `content`, prefix `ct`, branch label
   `content` in `dotmac_kernel.namespaces` in the same change as the manifest.
2. Add the `packages/dotmac-content` distribution, exact kernel dependency,
   public surface, typed errors/contracts, pure lifecycle and flush-only
   services.
3. Implement exactly five tenant tables named in the dossier. Fully qualify
   the schema; create composite tenant foreign keys and per-tenant uniques.
4. Add one independent `ct_0001_*` lineage root. In that migration create the
   schema, tables, forced RLS, policy and grants together. Declare only the
   tenant plane and its prerequisites.
5. Add no router, provider enum/client, person/member/role table, task table,
   file FK, delivery state or cross-module import.
6. Add service parity tests for plan/item CRUD, filtering, ordering, variants,
   creative relations and typed not-found/conflict errors. Services mutate and
   flush; kernel boundaries commit/rollback.
7. Add the Postgres cross-tenant canary first for every table and relation.

**Exit:** Gate 1 canaries plus focused unit, architecture and disposable-
Postgres integration tests green on Observer.

## Gate 3 — make it releasable

1. Add the package to the root exact path dependency set and lock with the
   repository-pinned Poetry version.
2. Update `docs/ARCHITECTURE.md` provenance/ownership tables and the module
   catalog in the same change.
3. Add the module release-lane entry only after the package surface, migration
   gate, compatibility declaration and changelog are complete.
4. On Observer run `make check`, `make test-unit`, then a disposable
   `make test-db-up && make test-integration && make test-db-down` sequence from
   a fresh worktree pinned to the candidate commit. Git-hosted CI remains merge
   acceptance evidence.

**Exit:** exact released package version and digest. No editable/path dependency
is evidence for Backoffice adoption.

## Gate 4 — Backoffice composition and data migration

1. In a Backoffice branch, pin the exact release and kernel floor; declare the
   tenant plane, migration owner and prerequisite binding; run its lineage in
   the Backoffice database.
2. Add product-owned thin adapters and declared permissions. Resolve actor and
   file references before calling the module. Keep membership/authorization
   decisions out of the module.
3. Build a total Mkt classifier. Preserve stable source-to-target identifiers
   and record every Campaign, Post, asset link, member relation and task
   disposition with no default bucket.
4. Backfill plans/items/variants/creative references idempotently. A `published`
   source row cannot cut over until the publishing workstream owns its release
   evidence.
5. Shadow reads for a complete editorial planning cycle. Reconcile text,
   states, dates, order and references; classify every mismatch.

**Exit:** zero unexplained drift, zero cross-tenant access, no unclassified
source rows, and all publishing-dependent rows explicitly gated.

## Gate 5 — switch and retire one writer at a time

Apply CNT-R1 through CNT-R9 in dependency order. For each row:

1. switch its only authorized write path to the module or named outside owner;
2. prove the shadow remains clean;
3. delete or make the old writer structurally unreachable;
4. lower the two-directional baseline in the same change; and
5. show the detector failing against a planted old-writer call.

Do not use a runtime flag that keeps both writers available. `PostService` and
the `Post` table cannot retire completely in this slice because they mix
publishing state; retire only the editorial fields whose new owner is live and
leave an explicit, counted publishing remainder.

**Exit:** Backoffice consumes the module in place of every selected editorial
writer. Update `EXTRACTION.toml` to `adopted` with Backoffice as the sole
`contract_consumer` only then.

## Gate 6 — independent Sub reuse

After Backoffice cutover, Sub may pin the same released contract, run its own
lineage and map its local editorial facts through product adapters. Any
cross-application synchronization uses versioned APIs/webhooks, a deduplicated
local observation and a local reconciler. No Backoffice row or database becomes
Sub's source of truth.

**Exit:** two real independent consumers permit `reuse-proven`; installation
alone does not.
