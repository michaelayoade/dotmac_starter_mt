# `dotmac-publishing` extraction and Backoffice adoption plan

**Status:** Gate 0 source pin and boundary frozen; Gate 1 RED canaries prepared.
No runtime implementation, release, composition or adoption has started.
**Source pin:** Mkt `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d`,
remote-verified on 2026-08-19.
**First adopter:** Backoffice. **Second candidate:** Sub, independently.

## Outcome

Ship one tenant-only `dotmac-publishing` module that owns immutable publication
requests, selected opaque targets, desired delivery time, retry-safe attempts,
normalized delivery observations and derived aggregate state. Backoffice adopts
an exact released version, migrates selected Mkt publication history, switches
each writer and deletes the old path. The slice is incomplete while Mkt and the
module can both decide the same release or outcome.

## Non-negotiable boundary

- Content and sites own their editable source/revisions. Publishing freezes a
  self-contained snapshot and imports neither sibling.
- Integrator owns installations, bindings, credentials, provider identity,
  connector execution, webhooks, retries/checkpoints and raw transport evidence.
- Publishing stores opaque target/receipt/remote references and its own local
  normalized observations. It contains no provider enum/client/credential.
- A typed timer port wakes due releases. Publishing does not import the Durable
  Timers module and does not poll in a task that constructs a session.
- Kernel idempotency and outbox remain their one owners. The publication effect
  and outbox intent are written in the same transaction.
- Backoffice owns actor authorization and target-selection policy.
- Modules never import siblings; applications never share databases, ORM,
  sessions, filesystems or writers.

## Gate 0 — freeze the evidence

1. Verify Mkt `main` still resolves to the accepted source revision.
2. Record exact source paths, behavior tests and all ten old-writer families.
3. Accept the intentional corrections: immutable snapshot before dispatch,
   explicit partial state, immutable attempts/observations, timer/outbox seams,
   revision rather than remote in-place edit, and withdrawal rather than hard
   delete.
4. Keep every row in
   `docs/inventories/publishing-writer-retirement.toml` at `not-started`.

**Exit:** one accepted source coordinate and no unresolved ownership term.

## Gate 1 — prove the canaries fail first

1. Commit only the dossier, retirement ledger, package `EXTRACTION.toml`, plan
   and architecture/lifecycle canaries.
2. On Observer, create a fresh isolated writable worktree pinned to that exact
   commit and install only from the committed lock with Poetry 2.4.1.
3. Run the focused source-audit, publishing architecture and publishing
   lifecycle tests. The publishing canaries must be RED because the distribution
   is absent; a pass or skip is a defect.
4. Record the exact commit, command, pass/failure count and intended missing-
   package reason in this plan and the dossier.

**Exit:** controlled RED tied to one immutable commit. No runtime code lands
before this evidence.

## Gate 2 — implement the smallest owner

1. On the current local suite line, allocate kernel a74 owner/short code/branch
   `publishing`, prefix `pb`, schema `mod_publishing`, in the same change as the
   manifest and root migration. Re-evaluate the alpha after any rebase.
2. Add `packages/dotmac-publishing` a1 with an exact compatible kernel floor,
   public contracts, typed errors, pure lifecycle/reconciler and flush-only
   services.
3. Implement exactly four tenant tables from the dossier with fully qualified
   schema, composite tenant foreign keys, per-tenant uniques and plain-string
   state columns.
4. Add independent `pb_0001_publishing`, declaring tenant scope, module roles,
   kernel idempotency and kernel outbox prerequisites. Create schema, tables,
   forced RLS, policy and exact grants together.
5. Create a release idempotently from a frozen canonical snapshot and distinct
   opaque targets. Schedule its generation through the typed timer port.
6. On due wake, create one monotonic attempt and kernel outbox event per eligible
   target in the same transaction. Never call provider code.
7. Accept deduplicated normalized observations, retain immutable attempt/outcome
   history and derive delivery/release state through one reconciler.
8. Add service parity tests for partial success, all failure, replay/conflict,
   retry, stale timer refusal, independent failures, revision and withdrawal.
9. Start with the disposable-Postgres cross-tenant canary across all four tables.

**Exit:** Gate 1 canaries plus focused unit, architecture and disposable-
PostgreSQL tests green on Observer.

## Gate 3 — make the candidate releasable

1. Add the exact root path dependency and regenerate the committed lock only
   with repository-pinned Poetry 2.4.1.
2. Update `docs/ARCHITECTURE.md`, module catalog, changelogs, compatibility and
   publication baseline in the same candidate.
3. Prove clean wheel/public surface, real kernel-floor install, consumer boot,
   migration composition and provider/sibling-import absence.
4. From a fresh exact Observer checkout run `make check`, full unit/architecture
   and disposable PostgreSQL integration. Git-hosted CI remains merge evidence.
5. Do not add a release allowlist entry or publish without separate authority.

**Exit:** a release-ready candidate with exact evidence, still unadopted.

## Gate 4 — Backoffice composition and total migration

1. Pin released kernel/publishing distributions in Backoffice without path or
   editable dependencies; declare and run its tenant plane and prerequisites.
2. Add thin guarded adapters that authorize actor and target selection before
   calling publishing.
3. Totally classify every Mkt Post, PostDelivery, Channel binding and remote
   action. Coordinate editorial rows with the content migration; use no default
   bucket.
4. Backfill releases, immutable snapshots, targets, attempt histories and
   normalized outcomes idempotently with stable source mappings.
5. Shadow one complete scheduled-publication cycle. Reconcile digest, target,
   desired time, attempt order, result, remote reference and aggregate state.

**Exit:** zero unexplained drift, zero unclassified source rows and zero cross-
tenant access.

## Gate 5 — switch and retire one writer at a time

Apply PUB-R1 through PUB-R10 in dependency order. For each row, switch the only
authorized path, prove the shadow remains clean, make the old path structurally
unreachable, lower the two-directional baseline in the same change and show the
detector failing against a planted old-writer call. Do not use a flag that keeps
both writers callable.

**Exit:** Backoffice consumes the package and every selected Mkt publication
writer/provider path is retired or has an explicitly named outside owner.
Only then may `EXTRACTION.toml` move from `audit-complete` to `adopted`.

## Gate 6 — independent Sub reuse

Sub may later pin the same released contract, run its own lineage and bind its
own local adapters/Integrator installation. Cross-application synchronization
uses versioned APIs/webhooks and deduplicated local observations; no Backoffice
row becomes Sub authority.

**Exit:** two independent real consumers permit `reuse-proven`.
