# Changelog — dotmac-entitlement-allocation

## 0.1.0a1 — 2026-08-12

First release. An immutable projection of what an activated contract version
entitles, extracted product-first from the vendor control plane.

**The module validates; the caller supplies the authority.** `stage_allocation`
takes a `CapabilityCatalogueReader` and performs the check itself — the kernel's
`grant_entitlement` pattern — so the invariant holds across every adapter rather
than once per adapter. There is no `validated=True`.

**Three couplings cut** relative to the source: the cross-module FK to
`contracts`, the direct `session.get(Contract, …)`, and the hard-coded
`"contract.activated"` literal. All three invert into one typed
`ContractSnapshot`, which is what makes the extraction independent of where
commercial contracts eventually live.

**`product_code` is persisted** — new relative to the source, and a correctness
fix: it closes the path where an allocation validated against one product is
issued as a licence for another. `allocation_product()` is how licence issuance
reads it.

**Replay does not re-validate.** An already-staged allocation is immutable
history whose legality was decided when it was staged; re-checking it against a
live catalogue would make a delivered entitlement unreplayable the day a
capability is retired.

Tables `allocations` + `allocation_entries` in `mod_ealloc`, lineage root
`ea_0001_allocations`. Platform catalog: no `tenant_id`, no RLS, `platform_api`
holds SELECT/INSERT only, `app_user` REVOKEd.

Requires `dotmac-kernel >= 0.1.0a43`.
