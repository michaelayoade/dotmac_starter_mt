# Changelog — dotmac-entitlement-allocation

## 0.1.0a3 — 2026-08-13

Allow the independent module's ORM models to coexist with same-named models in
its extraction source during a shadow phase. Relationship targets and ordering
now use class-bound callables instead of SQLAlchemy's global string registry,
so a consuming control plane can load both `Allocation` / `AllocationEntry`
pairs on the shared kernel `Base` without ambiguous mapper resolution. The
schema and domain contract are unchanged.

## 0.1.0a2 — 2026-08-13

Expose the installed Alembic lineage through the stable top-level
`versions_dir()` contract. A separate consuming repository can now compose the
module without deriving a private path from `__file__` or assuming a source
checkout layout. Domain behavior and schema are unchanged.

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
`ea_0001_allocations`. Platform catalog: no `tenant_id`, no RLS, `app_user`
REVOKEd. `platform_api` holds SELECT and INSERT, plus a COLUMN-LEVEL
`UPDATE (sealed, updated_at)` on `allocations` — the seal is the one decision
the online role may write, and every business column stays unwritable.

**Immutability includes append.** `platform_api` needs INSERT on
`allocation_entries` to stage at all, so the grant that makes the parent
immutable leaves the child appendable — raw SQL could add an unvalidated
capability to a staged allocation. A `refuse_late_entry` trigger rejects any
entry once the parent is SEALED,
and `quantity > 0` is a database CHECK rather than only a service rule.

The seal is EXPLICIT, not inferred from transaction identity. An earlier
revision tested `age(xmin) = 0` and rejected legitimate staging: the kernel's
at-most-once owner runs inside `conflict_savepoint`, and a SAVEPOINT is a
SUBTRANSACTION whose writes carry a subtransaction xid that never equals the
top-level one. `seal_is_one_way` makes the flip irreversible.

**Two identities, two protections.** `source_event_id` (a DELIVERY) goes through
the kernel's `execute_once_platform` — ADR-0014's one at-most-once owner — which
also means the staging audit event is written exactly once however many times an
at-least-once transport delivers. `(contract_ref, content_hash)` (an ACTIVATION)
carries a stored `snapshot_fingerprint`: the same pair arriving with a different
product, customer or entry set CONFLICTS rather than replaying. The fingerprint
lives on the allocation because idempotency records have a retention policy and
allocations do not.

**Duplicates are refused, not aggregated.** Deciding whether two lines of ten
seats mean twenty is a commercial rule owned by whoever owns contracts.

**Adapters translate.** The service catches nothing broad, so an adapter that
leaks its backing store's exception surfaces as a defect rather than being
relabelled an undeclared capability.

**Every delivery is recorded.** Every call enters the at-most-once owner before
anything else, including an activation replay. Otherwise a delivery key could be
spent without being recorded — stage claim A under event-a and claim B under
event-b, then replay claim A under event-b, and the call would succeed without
discovering that event-b belonged to a different request. An activation race
retries THROUGH the kernel, so the losing delivery key also receives its ledger
row.

**An unsealed allocation is not history.** Staging writes the parent, the
entries and the seal in one transaction, so this service cannot leave an
unsealed row behind by crashing; a committed one means raw SQL, an offline
repair, or a writer that split the sequence across transactions. Both
paths that consume an allocation — replay resolution and `allocation_product` —
raise `IncompleteAllocationError` rather than treating it as a fact, because the
row cannot say whether its missing entries were rejected or merely never
written.

**Fully typed contracts.** `AllocationStatus` and `AllocatedCapability` replace
a bare string and a positional pair; every public value object is frozen and
slotted with no mutable collection fields; the package type-checks strict.

Requires `dotmac-kernel >= 0.1.0a45`.
