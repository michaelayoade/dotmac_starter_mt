# Dotmac integration ecosystem programme

**Status:** Active execution ledger
**Branch:** `feat/integration-ecosystem-programme`
**Extraction base:** `origin/main` at `aac7a105`
**Architecture owners:** ADR-0024, AGENTS.md rule 28
**Started:** 2026-08-21

## Goal

Complete the reusable integration layer for the Dotmac ecosystem. The
independently deployed Integrator remains the only runtime that talks to
external providers. Starter owns the provider-neutral engine, capability
contracts and independently released connector distributions. Sub, ERP,
Academy and other products remain independent applications that synchronize
typed observations and commands through versioned ports; no application reads
another application's database or imports its models.

This is one consolidated **Starter** branch. It cannot also be one Git branch
in the independently deployed application repositories. Product pins,
receiver adapters, shadow evidence and legacy deletion therefore land as
separate adopter commits after this branch publishes its reusable artifacts.
That is deployment independence, not programme fragmentation.

## Terminal definition

The programme is complete only when all of the following are true:

1. `dotmac-integration` can execute every declared SPI mode without a database
   transaction spanning provider I/O. In particular, POLL has a real
   prepare/invoke/record/advance path rather than a protocol with no engine.
2. The connector portfolio contains independently releasable, provider-owned
   adapters for:
   - Meta WhatsApp — receive (already published);
   - Meta Social — receive (already published);
   - Paystack — settlement observation (already published), then reconcile;
   - Flutterwave API v4 only — settlement observation (already published),
     then reconcile;
   - Mono — bank transaction observations;
   - Remita — RRR/payment reconciliation;
   - LinkedIn — authenticated webhook challenge plus organization social-action
     and lead observations.
3. Every connector declares exact secret purposes and exact egress hosts,
   implements only the SPI, owns no database/session/product decision, carries
   an `EXTRACTION.toml`, and has conformance plus architecture sensitivity
   proofs.
4. Inter-application synchronization uses one provider-neutral transport
   pattern: local durable outbox -> authenticated versioned product port ->
   typed deduplicated observation -> local resolver/owner. There are no
   pairwise provider plugins such as `sub_to_erp` or `erp_to_academy` and no
   remote ORM/database access.
5. The first reusable inter-app capability flows are contract-proved:
   - Sub -> ERP: subscriber/service/billing observations; ERP decides its own
     accounting consequences.
   - ERP -> Sub: payment/ledger consequence observations needed by Sub; Sub
     decides service consequences.
   - ERP -> Academy: worker/learner eligibility observations; Academy decides
     enrolment and learning state.
   - Academy -> ERP: completion/credential observations; ERP decides workforce
     consequences.
6. The complete Starter stack passes Observer validation from a clean isolated
   worktree, then one PR reaches green GitHub CI and is merged.
7. Reusable artifacts are released in dependency order. Each adopter then
   exact-pins them, runs a shadow comparison, cuts over capability by
   capability, and deletes its direct provider surface while lowering the
   two-directional ratchet in the same change.
8. Large data migration extends the existing `dotmac-imports` owner: verified
   streaming CSV partition creation, immutable per-partition checksums/ranges,
   bounded worker claims and same-transaction outcome/checkpoint settlement.
   ERP remains the first pilot; the package stays unreleased until that proof.

## Non-goals and refusals

- No CRM connector or CRM cutover. CRM is retired.
- No ERPNext connector. The new composed ERP is a Dotmac product port, not an
  external provider.
- No generic `provider` flag, provider enum, provider import list or provider
  branch in the engine or Integrator assembly.
- No connector decides allocation, entitlement, lifecycle, status meaning,
  enrolment, accounting, subscriber state or another product's authority.
- No credential value in configuration, Git, logs, exceptions, fixtures or
  durable evidence; only declared binding names and secret references.
- No invented backwards compatibility. Flutterwave is v4 only; LinkedIn uses
  a currently supported monthly API version at deployment time rather than a
  hardcoded sunset version in reusable code.

## Build order and acceptance ledger

| Phase | Artifact / capability | Source mode | Acceptance | State |
|---|---|---|---|---|
| 0 | This programme ledger and exact product/source inventory | audit | all scope, owners, source revisions and stop conditions named | complete |
| 1 | `dotmac-integration` POLL execution seam | product-first from Sub engine + current SPI/checkpoint model | no session reaches plugin; atomic batch receipt; cursor advances only after durable receipt; stale advance refuses | complete; Observer acceptance green |
| 2 | `dotmac-connector-mono` `banking.transaction.observation.v1` | product-first from ERP Mono client/sync/tests | official v2 endpoint; lowest-denomination amounts preserved exactly; debit/credit remains provider evidence; off-origin pagination refused | complete; Observer acceptance green |
| 3 | `dotmac-connector-remita` `payments.reference.status.observation.v1` | product-first from ERP Remita client | official RRR status API; SHA-512 auth; provider status is carried verbatim, never interpreted by connector | complete; Observer acceptance green |
| 4 | `dotmac-connector-linkedin` social + lead observation ingress | greenfield after fleet inventory | exact-byte `X-LI-Signature`; HMAC challenge; one event per notification; official dedupe identities; no API-version default | complete; Observer acceptance green |
| 5 | Paystack + Flutterwave settlement-observation reconciliation | product-first from ERP/Sub clients | provider request/response translation only; no allocation/net amount/status consequence | complete; Observer acceptance green |
| 6 | `dotmac-app-sync` provider-neutral inter-app contracts | product-first from existing `ProductPortDescriptorSnapshot`, receipt delivery and application-directory contracts | reusable envelope, auth/idempotency/correlation; no product/provider branching; fake sender/receiver proves four flows | implemented; product pilot and release remain separate |
| 7 | `dotmac-imports` large-CSV partition lane | product-first extension of the existing Sub/ERP extraction after a pinned fleet inventory found no qualifying partition ledger | source verified before derivation; bounded immutable `dotmac-files` artifacts; contiguous descriptor plan; atomic expiring claims; checksum before domain call; validation cannot hold applier; stale claim refuses; promotion clones plan | implemented; ERP pilot and release remain separate |
| 8 | Documentation, release metadata and static policy | current release contracts | catalog, architecture, changelogs, lock, release allowlist and dependency floors agree | complete; canonical gate green |
| 9 | Observer + GitHub acceptance | exact branch commit | prescribed checks green; integration tests non-vacuous; one PR merged | Observer green; GitHub PR pending |
| 10 | Release and adopter cutovers | independent repositories | exact pins, shadow evidence, cutover, direct connector deletion and ratchet reduction | pending after merge |

## Source locks

The implementation may update these revisions only by recording the new pin and
re-running the relevant inventory. Dirty checkouts are never evidence; reads use
`git show` at the named commit.

| Source | Revision | Use |
|---|---|---|
| `dotmac_starter_mt` | `bfc112fc` | current engine, connector SPI, existing connector portfolio and contracts |
| `dotmac_erp` | `c656bb9070b7f35659f1968e44823e4727b309b9` | Mono and Remita production implementations and Mono parity tests |
| `dotmac_sub` | source revisions already pinned by each existing connector dossier | Paystack, Flutterwave, Meta and integration-engine parity |
| `dotmac_academy_app` | `a5e25e4e829350e503e66a03d73739529ba7da7f` | inter-app learner/completion requirement inventory only |
| `dotmac_integrator` | `3ecad2b8beed4dabf4809420b7f0ca04c8e200dd` | thin assembly capability; ingress, held-secret and ProductPort adapters already exist |
| `dotmac-integration-client` | `4714d9411e7512cc00944fd44583bc38812e9839` | reusable HTTP transport reference; import-namespace collision must be retired before adopter pinning |

## Current official protocol evidence

- Mono Financial Data uses `api.withmono.com`, the `mono-sec-key` header and
  `/v2/accounts/{id}/transactions`; amounts are returned in the currency's
  lowest denomination.
- Remita's first-generation RRR status endpoint authenticates with SHA-512 of
  `rrr + apiKey + merchantId`; provider status codes remain evidence because
  the product owns their consequence.
- LinkedIn validates a webhook with a `challengeCode` HMAC-SHA256 response and
  signs POST bodies in `X-LI-Signature` over the literal `hmacsha256=` prefix
  plus the exact raw JSON body. Organization social notifications deduplicate
  by `notificationId`; lead actions deduplicate by response URN plus
  `occurredAt`.

## Release and cutover order

1. Publish any required `dotmac-integration` alpha.
2. Publish connector distributions independently; publication creates no
   consumer and moves no authority.
3. Repair and release the `dotmac-integration-client` import namespace before
   any new product pin.
4. Pin engine and selected connector distributions in `dotmac_integrator`.
5. Publish each product's typed port descriptor and reconcile it in Integrator.
6. Mirror traffic while the old path is authoritative; persist queryable drift
   evidence, not log-only claims.
7. Activate one binding, move provider callbacks/schedules to Integrator, and
   keep product decisions local.
8. Delete the old provider client/secret/checkpoint/retry/webhook surface and
   lower that product's external-connector baseline in the same commit.

## Stop conditions

Implementation stops rather than guesses if an official protocol cannot be
verified, a product-first source cannot be read at its pinned revision, a
capability has no named owning product port, a connector would need product
meaning to proceed, or a release/cutover would make an independently deployed
consumer uninstallable.
