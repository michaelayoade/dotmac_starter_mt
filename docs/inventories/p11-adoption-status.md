# P11 — accepted production-lineage evidence

**As of:** 2026-08-17

**Status:** **P11 is MET.**

**Reference adopter:** `dotmac_vendor_control_plane` (platform plane)

**Accepted by:** Michael

**Decision:** [ADR-0017](../adr/0017-adoption-is-the-scarce-resource.md),
2026-08-17 amendment

This inventory is the current gate record. It replaces the 2026-08-14
measurement that correctly found P11 unmet at that time. The old snapshot is
preserved in Git history rather than left inline as contradictory current
guidance.

## 1. The accepted result

Vendor Control Plane runs the kernel's shipped migration lineage, three
independently released module lineages, and its product lineage in one real
production database. That is the exact exit condition in ADR-0017 decision 2.
It is not a Starter-only run, a package installation, a rehearsal, a stamp, a
copied migration, or a kernel model over a product-owned table.

The immutable production identity is:

| Field | Accepted evidence |
|---|---|
| Product source | `dotmac_vendor_control_plane` `main` at `f8f8c3fd636e663e4a17275c19e82fc1667aa52a` |
| Protected production deploy | [GitHub Actions run `32022599873`](https://github.com/michaelayoade/dotmac_vendor_control_plane/actions/runs/32022599873), conclusion `success`, exact `headSha` above |
| Target identity | `vendor-cp-prod`; service boundary `vendor.dotmac.io` |
| Immutable image | `ghcr.io/michaelayoade/dotmac_vendor_control_plane@sha256:56ec553139c449dc7da46a8873b3c03e95a61e43c970cd1675e28a202b2991cc` |
| Kernel | `dotmac-kernel==0.1.0a61` |
| Release Catalog | `dotmac-release-catalog==0.1.0a4` |
| Entitlement Allocation | `dotmac-entitlement-allocation==0.1.0a4` |
| Approvals | `dotmac-approvals==0.1.0a4` |
| Migration target | composed `heads` across kernel, Release Catalog, Entitlement Allocation, Approvals, and Vendor lineages |

The successful run recorded the same target identity and digest and completed
the deployment step. At the deployed source, `src/vendor_cp/migrations.py`
builds the five-lineage graph from the installed packages' public locators.
`scripts/deploy_production.sh` accepts only the immutable digest, verifies the
host and database-role contract, takes a pre-migration backup, invokes the one
composed migration owner, and starts the application only after the migration
and health check succeed. The migration adapter upgrades only composed `heads`
and requires the live connection to contain every composed head before commit.

## 2. Adoption witnesses

Starter merge commit `93aecc800be5258bebe7e3cb3ef68097b0a8a113`
([PR #233](https://github.com/michaelayoade/dotmac_starter_mt/pull/233))
accepted the product adoption evidence. These dossiers independently carry the
same Vendor revision, deploy run, and immutable image:

| Distribution | Current state | Production authority evidence |
|---|---|---|
| `dotmac-release-catalog` | `adopted` | Vendor is the only release-evidence writer; a real Sub release artifact and attestation were ingested through the module owner |
| `dotmac-entitlement-allocation` | `adopted` | `mod_ealloc` is live; Vendor's local tables and writer are retired; the platform role owns effective DML and `app_user` has none |
| `dotmac-approvals` | `adopted` | `mod_approvals` is live on the selected platform plane; Vendor's local policy/decision tables and writer are retired; `app_user` has no module privileges |

The architecture test
`tests/architecture/test_p11_production_lineage_evidence.py` cross-checks this
record and ADR-0017 against all three `EXTRACTION.toml` dossiers. Its
sensitivity proof removes every required claim and mutates each dossier's
state, consumer, and deployment evidence to prove the guard fails on drift.

## 3. Clause-by-clause acceptance

| ADR-0017 requirement | Evidence | Result |
|---|---|---|
| The kernel's migration lineage | Vendor pins the released kernel and composes `kernel_versions_dir()` into its one graph | MET |
| Runs in a product database | Vendor is an independently deployed product assembly with its own database, runtime, migrations, and authorization | MET |
| In production | Protected run `32022599873` deployed the recorded immutable image to `vendor-cp-prod` and completed the migration/health path | MET |
| Not Starter self-proof | The adopter source, workflow, image, database, and contract consumers are all Vendor-owned | MET |
| Not a rehearsal or stamp | The production entrypoint executes an upgrade to composed `heads`; it has no stamp path and refuses partial targets | MET |

## 4. What P11 does and does not release

P11 was the general moratorium gate for starting shared stateful facilities. Its
accepted production proof removes that gate. It does not make a proposed
module complete, adopted, or safe to cut over.

Sub tenant-plane / RLS proof remains a separate adoption track. It still owns
its tenant-scope decisions, RLS/FORCE/grant proof, backfill, shadow comparison,
reconciliation, and writer-retirement gates. A module targeting Sub must prove
those properties in Sub even though the fleet-wide P11 gate is met.

For `dotmac-sales`, this means package and lineage implementation may now begin
from the accepted Sub source audit and red canaries. It does not waive the
product-first dossier, accepted-quote boundary, tenant isolation, exactly-once
acceptance, immutable handoff, Sub adoption, or CRM writer-retirement proofs.
It authorizes no deployment, merge, production cutover, or data deletion.

Documents dated before this accepted record may still describe P11 as `UNMET`.
Those statements are historical measurements, not current gates. When a
downstream workstream next changes such a document, it should replace the stale
P11 status and evaluate its remaining module-specific blockers honestly; it
must not reinterpret this record as proof that those other gates are met.

## 5. Re-verification without production access

The accepted evidence can be checked without opening a production session:

```bash
git -C <vendor> show f8f8c3fd636e663e4a17275c19e82fc1667aa52a:pyproject.toml
git -C <vendor> show f8f8c3fd636e663e4a17275c19e82fc1667aa52a:src/vendor_cp/migrations.py
git -C <vendor> show f8f8c3fd636e663e4a17275c19e82fc1667aa52a:scripts/deploy_production.sh
gh run view 32022599873 \
  --repo michaelayoade/dotmac_vendor_control_plane \
  --json headSha,status,conclusion,url,jobs
```

Then read the three module dossiers and run the architecture test named above.
A later deployment does not erase this first-adopter proof. If any immutable
identity here is shown to be wrong, correct or revoke the accepted record in a
new reviewed change; never silently weaken the evidence contract.
