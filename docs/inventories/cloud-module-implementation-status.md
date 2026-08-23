# Cloud module implementation status

**As of:** 2026-08-19  
**Committed module train inspected:** `f54ea0d`  
**Scope:** local `dotmac_starter_mt` Git refs and worktrees

This inventory answers which Cloud owners have an implementation, not which
ones have been released or adopted. It supplements the branch-local
[`MODULE_CATALOG.md`](../MODULE_CATALOG.md): a generated catalogue can only
report packages present in its own Git tree and therefore cannot describe
uncommitted work owned by another active worktree.

The inspected module train had not landed on `origin/main`: at the scan it was
29 commits ahead and 10 behind. The committed rows below are therefore durable
branch evidence, not a claim about the default branch or a released artifact.

## Status vocabulary

- **Implemented** — a committed distribution contains its contract, public
  surface, manifest, migration lineage where stateful, owning service and
  focused proof suites.
- **Active WIP** — those artifacts exist only as uncommitted work in a named
  worktree. This is not durable implementation evidence yet.
- **Source audit only** — the checked-in boundary/evidence exists but no package
  directory exists in any inspected local ref or worktree.
- **Released** — an immutable release record and exact tag exist. A release
  allowlist row alone is not a release.
- **Adopted** — a real assembly exact-pins the release, composes the selected
  lineage, switches the authoritative writer and retires the displaced local
  owner. Package existence is not adoption.

## Cloud owner ledger

| Owner | Implementation state | Release/adoption state | Evidence and next gate |
|---|---|---|---|
| `dotmac-billing` | **Implemented** on the committed module train | `audit-complete`; release-allowlisted but not tagged; `contract_consumers = []` | Dual-plane `bi_0001`, owning service and focused architecture/unit/PostgreSQL suites exist. Validate the exact train on Observer, then separately authorize kernel `0.1.0a75` and Billing `0.1.0a1` release before either product cutover. |
| `dotmac-orders` | **Implemented** on the committed module train | `audit-complete`; unallowlisted and untagged; `contract_consumers = []` | Tenant `or_0001`, immutable order snapshots, owning service and focused suites exist. Adoption remains blocked on a mechanically translatable Sales accepted-Quote handoff and a named owner-decided eligibility fact, or removal of the financial gate. |
| `dotmac-subscriptions` | **Implemented** on the committed module train | `audit-complete`; unallowlisted and untagged; `contract_consumers = []` | Dual-plane `su_0001`, offer/contract/recurrence services and focused suites exist. Validate the exact train; Vendor CP remains platform-plane cutover 1 and Sub tenant-plane cutover 2. |
| `dotmac-collections` | **Implemented** on the committed module train | `audit-complete`; unallowlisted and untagged; `contract_consumers = []` | Tenant `cl_0001`, canonical flush-only persistence and PostgreSQL isolation/concurrency/replay proofs exist. Validate the exact train, then prepare Sub backfill, shadow, rollback and sealed writer retirement. |
| `dotmac-domains` | **Active WIP** in `agent/dotmac-domains`, based at `1c33910`, with a related stacked worktree based at `abef05a` | Uncommitted; no release or adopter claim | Package, `do_0001`, service and focused tests exist in the active worktrees. The owning session must reconcile the two worktrees, finish its Observer proofs and commit before this row can become Implemented. |
| `dotmac-hosting` | **Source audit only** | No package, tag or consumer | [`hosting-sources.md`](hosting-sources.md) proves the greenfield boundary. Construction follows the Domains contract and starts from lifecycle/failure/reconciliation canaries. |
| `dotmac-fulfillment` | **Source audit only** | No package, tag or consumer | [`fulfillment-sources.md`](fulfillment-sources.md) proves Sub has no qualifying saga engine. Construction is greenfield on the kernel provisioning-participant contract. |
| `dotmac-document-rendering` | **Source audit only** | No package, tag or consumer | [`document-rendering-sources.md`](document-rendering-sources.md) and its extraction dossier define the stateless renderer. Billing now supplies `InvoiceDocumentFactV1` and the official-artifact recording seam, closing part of the earlier contract gate; the qualifying ERP source review and explicit ADR-0017 demand gate still precede package creation. |

## Related active foundation slice

`dotmac-party` is not a Cloud business owner, but it was part of the same status
review. Its candidate package exists as uncommitted work in `feat/dotmac-party`
based at `6c97175`. It remains intentionally unreleased and uncomposed with no
contract consumer; Sub backfill, reader cutover, shadow parity and sealed local
writer retirement remain the adoption gate.

## Refresh method

The scan enumerated `git worktree list --porcelain`, inspected every local
`packages/*/EXTRACTION.toml`, and searched `git rev-list --all --objects` for
the three absent distribution paths. Refresh this inventory whenever one of
the WIP packages commits, a module train lands, or a release/adoption changes.
Do not infer programme state from one checkout's generated catalogue.
