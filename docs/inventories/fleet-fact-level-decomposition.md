# Fact-level ownership — declarations, and a reachability heuristic over them

**Status:** declaration counts are measured; **ownership conclusions are not**.
**As of:** 2026-08-12 · **ERP** `0f4b1698` · **CRM** `c64b5aa0` · **Sub** `9f6f9f36`
**Measured by:** `scripts/fleet_fact_registry.py` (`make fleet-facts`)
**Registry:** [`fleet-fact-registry.json`](fleet-fact-registry.json) — carries its own
provenance block (date, revisions, detector schema version, normalization rules,
and an explicit `proves` / `does_not_prove` pair)

[`fleet-decomposition-matrix.md`](fleet-decomposition-matrix.md) answers *which
capability family is duplicated*. This file exists to go finer — to ask whether
each fact has a named owner — and to be honest that **the current detector
cannot answer that question**.

## What is measured, and what is only detected

The unit is the one the source-of-truth standard uses: a business fact or state
transition with exactly one named authoritative writer. Sub declares these
directly — `app/services/sot_registry/` names 426 services, the module
implementing each, and the facts each `owns`, validated by Sub's own registry
tests. ERP has a smaller `sot_relationships.py` of the same shape. CRM has
neither.

Extracting those declarations is sound. Relating them to tables is not, yet:

> The detector counts `owns` strings **without retaining their identities**,
> separately scans `from app.models... import Model` statements, and marks every
> imported model as linked. **It never associates a particular fact with a
> particular table.**

Two consequences, and they cut both ways:

- A direct import may be an **input or a projection**, not a write. So an edge
  does not establish ownership.
- A real writer may reach its table **indirectly through a repository or
  helper**, leaving no edge. So the absence of an edge does not establish the
  absence of an owner.

**Neither direction is a reliable ownership bound.** An earlier revision of this
document claimed the unlinked direction was reliable and quoted "711 tables have
no declared owner". That claim exceeded the detector and is withdrawn.

## Declaration counts — measured

| | Sub | ERP | CRM |
|---|---:|---:|---:|
| Declared domains | 30 | 9 | 0 |
| Declared services | 426 | 25 | 0 |
| **Declared facts** | **1,392** | **41** | **0** |
| Tables | 576 | 397 | 218 |

1,433 facts are declared fleet-wide and **97% of them are Sub's**. ERP's registry
is a Phase-0 seed covering tenancy, identity, configuration, audit, GL, platform
events, licensing and sync; it does not reach finance detail, HR, payroll,
inventory or assets. CRM declares nothing.

That asymmetry is the finding. Fact-level decomposition is largely authored for
Sub, barely started for ERP, and absent for CRM.

## Direct-import reachability — detected, not proven

| | Sub | ERP | CRM |
|---|---:|---:|---:|
| Tables with a direct import edge from a declared service | 428 | 52 | 0 |
| Tables with **no detected edge** | 148 | 345 | 218 |

711 tables fleet-wide have no detected edge. Read that as **a detection gap, not
an ownership gap** — it is the set to look at, sized, and nothing more.

## The triage queue

Of the 125 exactly duplicated table names, **28 have no detected direct import
edge from any declared service in any product**. That is a screening result, and
these 28 are a **high-priority manual triage list** — not a proven set of
unowned facts.

| Family | Tables (CRM ↔ Sub in every case) |
|---|---|
| positioning (1) | `field_tech_location_pings` |
| field-workforce (8) | `availability_blocks`, `eta_updates`, `field_map_asset_location_provenance`, `installation_project_notes`, `shifts`, `skills`, `technician_skills`, `work_links` |
| outside-plant (6) | `buildout_milestones`, `buildout_requests`, `buildout_updates`, `olt_power_units`, `olt_sfp_modules`, `wireless_masts` |
| geospatial-qualification (3) | `coverage_areas`, `geo_layers`, `service_qualifications` |
| sales-agreements (3) | `contract_signatures`, `document_sequences`, `legal_documents` |
| identity-access (2) | `device_tokens`, `vendor_users` |
| ticketing-sla (2) | `queue_mappings`, `sla_targets` |
| analytics-reporting (1) | `kpi_aggregates` |
| settings (1) | `kpi_configs` |
| integration-external (1) | `external_references` |

Triage asks one question per row: **does a declared fact already govern this
state, reached indirectly — or is there genuinely no owner?** Only the second
answer is a finding, and only triage can tell them apart.

The consequence for sequencing holds either way, because it is conditional:
where triage confirms no owner, `consolidate → Sub` would move a fact from one
unowned table to another and call it consolidation. Declaring the owner is then
the prerequisite for both halves of that row — the consolidation *and* the
later product-first sourcing of the Starter module.

## CRM's zero is not uniformly intentional

An earlier revision said CRM declares nothing "because it is being retired" and
treated that as correct across the board. It is correct for only one of three
classes, and CRM remains the extraction source for legitimate acquisition,
opportunity, campaign and engagement capabilities.

| Class | Meaning | Declaration needed? |
|---|---|---|
| `retirement_source` | Duplicate operational state Sub is authoritative for | **No.** Do not author ownership declarations merely to retire something. |
| `extraction_source` | A legitimate CRM-owned capability that becomes a Starter module | **Yes.** Ownership characterization is a prerequisite for moving it. |
| `projection` | CRM only consumes another owner's fact | **No**, but the source must be named and the copy made rebuildable. |

Sizing the classes, honestly:

- **123** of CRM's 218 tables are exact-name duplicates of Sub or ERP tables —
  the `retirement_source` candidate pool.
- **95** are exact-name exclusive to CRM. At least **23** of those are namespace
  aliases of Sub tables (`crm_conversations` ↔ `inbox_conversations` and
  friends, measured in the matrix), so they are duplicates too. That leaves **at
  most 72** tables as the combined `extraction_source` + `projection` pool.
- The programme frame's independent read of CRM's retirement ledger — **~11 of
  73 web modules justify a module row** — is the same shape from the other
  direction.

**Which of the ≤72 are extraction sources is not measured here and must be
adjudicated.** Table exclusivity is a candidate signal, not a classification.

## The target: explicit product-side linkage

These counts are a stopgap. The fix is for each declared fact to carry its
linkage in the product that owns it:

| Field | Purpose |
|---|---|
| `fact_id` | Stable identifier, survives renames and reordering |
| `owner_service_id` | The one declared service that owns the decision |
| state / table / external reference | What the fact actually governs |
| `role` | `authoritative` \| `observation` \| `projection` \| `input` |
| owned transitions | The state changes this fact's owner decides |
| evidence / tests | The behavioural proof |

With `role` present, the ambiguity this document is built around disappears:
an import edge to an `input` or `projection` stops reading as ownership.

Division of labour, unchanged: **Sub remains authoritative for its own
declarations.** Starter collects stable IDs, counts and references rather than
copying 1,392 fact strings — duplicating them here would replicate a build
inside a document about not replicating builds. **Governance adjudicates
collisions using those IDs.**

## Keeping this honest

```sh
python scripts/fleet_fact_registry.py           # report
python scripts/fleet_fact_registry.py --check   # ratchet
python scripts/fleet_fact_registry.py --write
```

Two-directional ratchet (ADR-0018): detected edges may not regress, the triage
queue may only shrink, and an improvement fails until it is recorded. Provenance
is excluded from the comparison — the date and revisions change every run — and
a `detector_schema_version` bump forces a rewrite rather than comparing numbers
produced a different way.

A missing repository is UNMEASURED, never zero. `tests/architecture/
test_fleet_fact_registry.py` keeps this document and the registry in sync,
including the caveat above; it does not re-measure, because the source monoliths
are absent from this repository's CI.
