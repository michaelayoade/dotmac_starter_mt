# Vendor platform and deployment

- Short explanations of package responsibilities, not a claim of production readiness.
- Check the [catalog](../MODULE_CATALOG.md) for installation, release and adoption evidence.
- A **tenant** is a customer workspace; the **platform** is the operator control plane.
- [All module groups](index.md).

## Commercial agreements (`dotmac-commercial-agreements`)

- **What it does:** Records commercial agreements, accepted terms and lifecycle evidence.
- **Use it when:** Managing the vendor's agreement with an operator or counterparty.
- **Keep in mind:** Agreement acceptance alone is not a license issuance or deployment.
- **Details:** [Package guide](../../packages/dotmac-commercial-agreements/README.md) · [Source and contract](../../packages/dotmac-commercial-agreements/EXTRACTION.toml).

## Entitlement allocation (`dotmac-entitlement-allocation`)

- **What it does:** Projects the capabilities promised by an activated agreement.
- **Use it when:** Preparing an immutable entitlement allocation for licensing decisions.
- **Keep in mind:** The allocation is not itself an executable grant or software license.
- **Details:** [Package guide](../../packages/dotmac-entitlement-allocation/README.md) · [Source and contract](../../packages/dotmac-entitlement-allocation/EXTRACTION.toml).

## Licensing (`dotmac-licensing`)

- **What it does:** Issues signed, versioned and revocable software-use grants.
- **Use it when:** Operating the vendor side of commercial software licensing.
- **Keep in mind:** The application's license receiver is separate; this package does not become every product's runtime issuer.
- **Details:** [Package guide](../../packages/dotmac-licensing/README.md) · [Source and contract](../../packages/dotmac-licensing/EXTRACTION.toml).

## Release catalog (`dotmac-release-catalog`)

- **What it does:** Records published software artifact digests and supporting attestations.
- **Use it when:** Identifying exactly which artifact was released and its evidence.
- **Keep in mind:** It stores evidence references, not artifact bytes or deployment choices.
- **Details:** [Package guide](../../packages/dotmac-release-catalog/README.md) · [Source and contract](../../packages/dotmac-release-catalog/EXTRACTION.toml).

## Deployment control (`dotmac-deployment-control`)

- **What it does:** Owns desired deployment state, rollout plans, acknowledgements and drift reconciliation.
- **Use it when:** Deciding what a managed deployment should run.
- **Keep in mind:** It does not replace the execution facility or manufacture authorization evidence.
- **Details:** [Package guide](../../packages/dotmac-deployment-control/README.md) · [Source and contract](../../packages/dotmac-deployment-control/EXTRACTION.toml).

## Deployment planning and execution facility (`dotmac-deployment-foundation`)

- **What it does:** Validates deployment declarations and derives build/deploy artifacts and execution plans.
- **Use it when:** Giving assemblies a consistent deployment facility.
- **Keep in mind:** Execution needs the required verified authorization; a rendered plan is not permission to deploy.
- **Details:** [Package guide](../../packages/dotmac-deployment-foundation/README.md) · [Source and contract](../../packages/dotmac-deployment-foundation/EXTRACTION.toml).

## Platform health (`dotmac-platform-health`)

- **What it does:** Records authenticated runtime-health observations and freshness summaries.
- **Use it when:** Supplying platform-wide health evidence for operational decisions.
- **Keep in mind:** It does not own raw telemetry, support tickets or rollout decisions.
- **Details:** [Package guide](../../packages/dotmac-platform-health/README.md) · [Source and contract](../../packages/dotmac-platform-health/EXTRACTION.toml).

## Support access (`dotmac-support-access`)

- **What it does:** Tracks temporary platform support access, consent and access evidence.
- **Use it when:** Granting narrowly scoped vendor support access for a stated purpose.
- **Keep in mind:** It does not grant permanent access or replace approval authority.
- **Details:** [Package guide](../../packages/dotmac-support-access/README.md) · [Source and contract](../../packages/dotmac-support-access/EXTRACTION.toml).

## Brand profiles (`dotmac-brand-profiles`)

- **What it does:** Stores and resolves versioned brand identity, colors, asset references and host bindings.
- **Use it when:** Serving several brands from a shared released product.
- **Keep in mind:** It stores brand data; UI assets and rendering have their own owners.
- **Details:** [Package guide](../../packages/dotmac-brand-profiles/README.md) · [Source and contract](../../packages/dotmac-brand-profiles/EXTRACTION.toml).

## Numbering (`dotmac-numbering`)

- **What it does:** Allocates document numbers from declared series and keeps allocation receipts.
- **Use it when:** Issuing traceable identifiers without duplicate allocation.
- **Keep in mind:** A number does not create, approve or issue the business document itself.
- **Details:** [Package guide](../../packages/dotmac-numbering/README.md) · [Source and contract](../../packages/dotmac-numbering/EXTRACTION.toml).

## Runner network transport (`dotmac-runner-transport`)

- **What it does:** Validates runner network capabilities and renders restricted host transport policy.
- **Use it when:** Controlling network transport for self-hosted CI runners.
- **Keep in mind:** Runner transport permission is distinct from a workflow's workload network permission.
- **Details:** [Package guide](../../packages/dotmac-runner-transport/README.md) · [Source and contract](../../packages/dotmac-runner-transport/EXTRACTION.toml).

## GitHub Actions transport adapter (`dotmac-runner-transport-github-actions`)

- **What it does:** Maps pinned GitHub endpoint evidence to runner transport capabilities.
- **Use it when:** Supplying GitHub-specific transport declarations to the shared facility.
- **Keep in mind:** Only complete supported endpoint groups are admitted; it is not a general network allowlist.
- **Details:** [Package guide](../../packages/dotmac-runner-transport-github-actions/README.md) · [Source and contract](../../packages/dotmac-runner-transport-github-actions/EXTRACTION.toml).
