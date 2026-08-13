# Product-manifest publication sources

**Measured:** 2026-08-13
**Scope:** `dotmac_starter_mt`, `dotmac_erp`, `dotmac_sub`, `dotmac_crm`,
`dotmac_vendor_control_plane`
**Question:** which product already publishes a deterministic, release-bound
statement of its stable product code and manifest-declared capabilities?

This inventory exists for ADR-0006's product-first extraction rule. It separates
three things that are easy to collapse into one misleading "product catalogue":

1. the reusable mechanism that derives declared capability codes from an
   assembly's installed manifests;
2. each product's own declarations; and
3. the vendor's commercial mapping of offers and contracts to a product.

Only the first is a kernel contract. The second stays in each product assembly.
The third stays in the Vendor Control Plane.

## Mechanical sweeps

Against each repository's refreshed `origin/main`:

```text
git grep -n -E 'ProductAssemblySpec\(|CapabilityCatalogue\.from_manifests|\
install_capabilities|capabilities[[:space:]]*=' origin/main

git ls-tree -r --name-only origin/main | rg \
'(^|/)(assembly|composition|feature|manifest|release).*\.(py|json|ya?ml)$|\
\.github/workflows'
```

The first sweep finds executable assembly/capability declarations. The second
finds possible publication paths even when they use different vocabulary. The
audit then read each hit that could own the scoped question; unrelated device,
connector, import-batch and UI asset manifests were excluded by meaning, not by
filename.

## Findings

| Repository | Assembly identity | Capability source | Release-bound product snapshot | Classification |
|---|---|---|---|---|
| Starter | `ProductAssemblySpec.name`; kernel builds `CapabilityCatalogue` from installed manifests | kernel contract and reference assembly | none | reusable mechanism exists, publication contract missing |
| Sub | `app/composition.py::PRODUCT_NAME = "dotmac-sub"`; `SUB_ASSEMBLY` | five codes across four `FeatureManifest`s, validated by `CAPABILITY_CATALOGUE` and architecture tests | none; release evidence binds OCI/source/migration facts but carries no product capability document | **qualifying production-shaped source** |
| ERP | no product assembly on `origin/main`; only `dotmac-erp-probe` in a kernel compatibility test | probe-only `erp_probe.use`; runtime module enablement remains an ERP-local set | none | future adopter, not a source for the document shape |
| CRM | no kernel dependency, `ProductAssemblySpec`, or manifest-declared capability catalogue on `origin/main` | none in the scoped meaning | none | future adopter after assembly adoption |
| Vendor Control Plane | `ASSEMBLY_NAME = "dotmac-vendor-control-plane"` for its own assembly | target-product codes are reconstructed from `VENDOR_PRODUCT_MANIFEST_CAPABILITIES_JSON` | none; configured lists are neither release-bound nor produced by target assemblies | blocked consumer and temporary bridge |

### The source we preserve

Sub's `app/composition.py` is the only real product declaration in scope. Its
load-bearing behavior is:

- stable product identity is assembly-owned (`PRODUCT_NAME`);
- capability codes are declared on the owning manifests;
- `CapabilityCatalogue.from_manifests` is the one validator;
- catalogue construction is deterministic and import-safe; and
- the declaration is metadata, not an entitlement, permission, or runtime
  product-database query.

The kernel contract preserves those behaviors and generalizes only at the
release seam: a caller supplies the product release version, and the kernel
produces one canonical JSON document plus its content digest.

## Ownership ruling

| Decision/fact | Owner |
|---|---|
| Snapshot schema, canonical encoding, parsing, digest, assembly-to-snapshot derivation | `dotmac_kernel.product_manifest` |
| Product code and manifest capability declarations | each product assembly |
| Exact artifact bytes and association to an attestation digest | `dotmac-release-catalog` plus the product release pipeline |
| Tenant-to-application-instance binding | `dotmac-application-directory` |
| Offer/contract mapping, legacy row evidence, and cutover preflight | Vendor Control Plane |

The kernel does **not** gain a database table, product registry, network client,
release selector, or list of Dotmac products. `dotmac-application-directory`
does not gain product-catalogue authority, and `dotmac-release-catalog` continues
to store an attestation pointer rather than interpreting its document.

## First cutover and retirement

The Vendor Control Plane is blocked today from adopting
`dotmac-entitlement-allocation`: it cannot prove that configured capability
lists came from the named target product release. Its current
`VENDOR_PRODUCT_MANIFEST_CAPABILITIES_JSON` and local
`ProductCapabilityCatalogueReader` are expand/shadow scaffolding only.

The first cutover is complete only when:

1. Sub's release path emits this canonical document from `SUB_ASSEMBLY`;
2. the document digest is associated with Sub's exact release artifact;
3. Vendor verifies and consumes that document through the published
   `dotmac_entitlement_allocation.CapabilityCatalogueReader` boundary; and
4. the raw capability-list configuration and duplicate Vendor protocol/error
   vocabulary are deleted.

ERP and CRM do not invent snapshots before they possess real assembly
declarations. They adopt the same contract when their product manifests become
authoritative.
